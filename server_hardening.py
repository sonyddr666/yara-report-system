from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import sqlite3
import threading
import traceback
from collections import defaultdict
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import server_core as core

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
IMAGE_DIR = DATA_DIR / "images"
DB_PATH = DATA_DIR / "database.db"
SCHEMA_PATH = ROOT / "schema.sql"
SNAPSHOT_DIR = DATA_DIR / "snapshots"

_REPORT_LOCKS: defaultdict[str, threading.RLock] = defaultdict(threading.RLock)
_DEVICE_LOCK = threading.RLock()
_RESTORE_LOCK = threading.RLock()


class RevisionConflict(RuntimeError):
    def __init__(self, current_revision: int):
        super().__init__("Os dados do servidor foram alterados por outra sessão.")
        self.current_revision = current_revision


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe_text(value) -> str:
    return str(value or "").strip()


def normalize_ip(value) -> str:
    return safe_text(value).lower()


def content_hash(value) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})")}


def ensure_column(conn: sqlite3.Connection, table: str, name: str, definition: str) -> None:
    if name not in table_columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def migrate_db(conn: sqlite3.Connection) -> None:
    ensure_column(conn, "equipments", "possui_md410", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(conn, "equipments", "base_model", "TEXT NOT NULL DEFAULT 'md400'")
    ensure_column(conn, "reports", "revision", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(conn, "reports", "content_hash", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "report_items", "entry_key", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "report_items", "device_ip", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "report_items", "attended_type", "TEXT NOT NULL DEFAULT ''")

    duplicate_groups = conn.execute(
        """
        SELECT report_id, entry_key
        FROM report_items
        WHERE trim(entry_key) <> ''
        GROUP BY report_id, entry_key
        HAVING COUNT(*) > 1
        """
    ).fetchall()
    for group in duplicate_groups:
        rows = conn.execute(
            "SELECT id FROM report_items WHERE report_id=? AND entry_key=? ORDER BY id",
            (group["report_id"], group["entry_key"]),
        ).fetchall()
        for row in rows[1:]:
            conn.execute("UPDATE report_items SET entry_key='' WHERE id=?", (row["id"],))

    conn.execute("CREATE INDEX IF NOT EXISTS idx_report_items_device_ip ON report_items(device_ip)")
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_report_items_entry
        ON report_items(report_id, entry_key)
        WHERE trim(entry_key) <> ''
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS device_sync (
          id INTEGER PRIMARY KEY CHECK(id=1),
          revision INTEGER NOT NULL DEFAULT 0,
          content_hash TEXT NOT NULL DEFAULT '',
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute("INSERT OR IGNORE INTO device_sync(id) VALUES(1)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_images_context ON images(day_key, equipment_id, kind, sha256)"
    )


def init_db() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        migrate_db(conn)
        conn.commit()


def safe_json_response(handler, status: int, payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    try:
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Cache-Control", "no-store")
        handler.end_headers()
        handler.wfile.write(body)
    except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError):
        return


def contextual_store_image(
    conn: sqlite3.Connection,
    day_key: str,
    equipment_id: int | None,
    kind: str,
    content: bytes,
    mime_type: str,
    original_name: str = "",
) -> tuple[int, bool]:
    digest = hashlib.sha256(content).hexdigest()
    existing = conn.execute(
        """
        SELECT id FROM images
        WHERE day_key=? AND equipment_id IS ? AND kind=? AND sha256=?
        ORDER BY id LIMIT 1
        """,
        (day_key, equipment_id, kind, digest),
    ).fetchone()
    if existing:
        return int(existing["id"]), True

    day_dir = IMAGE_DIR / day_key
    day_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(original_name).suffix.lower() if original_name and Path(original_name).suffix else core.EXT_BY_MIME.get(mime_type.lower(), ".jpg")
    filename = f"equipment-{equipment_id or 'unknown'}-{kind}-{digest[:12]}{ext}"
    target = day_dir / filename
    target.write_bytes(content)
    rel_path = target.relative_to(DATA_DIR).as_posix()
    cur = conn.execute(
        """
        INSERT INTO images(day_key,equipment_id,kind,original_name,file_path,sha256,mime_type,size_bytes)
        VALUES(?,?,?,?,?,?,?,?)
        """,
        (day_key, equipment_id, kind, original_name, rel_path, digest, mime_type, len(content)),
    )
    return int(cur.lastrowid), False


def update_image(
    conn: sqlite3.Connection,
    day_key: str,
    item: dict,
    item_id: int,
    equipment_id: int,
    field: str,
    kind: str,
    column: str,
) -> tuple[str | None, str | None]:
    if bool(item.get(f"{field}Removed")):
        conn.execute(f"UPDATE report_items SET {column}=NULL WHERE id=?", (item_id,))
        return None, None
    value = safe_text(item.get(field))
    if not value:
        return None, None
    image_data = core.image_bytes_from_value(value)
    if image_data is None:
        return None, f"{field} não foi reconhecida"
    image_id, reused = contextual_store_image(
        conn, day_key, equipment_id, kind, image_data[0], image_data[1], image_data[2] or f"{field}.jpg"
    )
    conn.execute(f"UPDATE report_items SET {column}=? WHERE id=?", (image_id, item_id))
    if reused:
        return None, None
    row = conn.execute("SELECT file_path FROM images WHERE id=?", (image_id,)).fetchone()
    return str(row["file_path"]), None


def report_hash_payload(report: dict) -> dict:
    copy = json.loads(json.dumps(report, ensure_ascii=False))
    copy.pop("updatedAt", None)
    copy.pop("revision", None)
    copy.pop("contentHash", None)
    for item in copy.get("equipment") or []:
        item.pop("beforeImage", None)
        item.pop("afterImage", None)
    return copy


def save_report(report: dict, mode: str = "replace") -> dict:
    day_key = core.safe_day_key(report.get("dayKey") or core.br_to_iso(report.get("header", {}).get("dataRelatorio")))
    report = dict(report)
    report["dayKey"] = day_key
    expected = report.get("expectedRevision")
    saved_images: list[str] = []
    warnings: list[str] = []

    with _REPORT_LOCKS[day_key], connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        previous = conn.execute("SELECT id,revision FROM reports WHERE day_key=?", (day_key,)).fetchone()
        current_revision = int(previous["revision"] or 0) if previous else 0
        if expected is not None and int(expected) != current_revision:
            conn.rollback()
            raise RevisionConflict(current_revision)

        report_id = core.upsert_report(conn, report)
        existing_rows = conn.execute(
            "SELECT * FROM report_items WHERE report_id=? ORDER BY position,id", (report_id,)
        ).fetchall()
        by_entry = {safe_text(row["entry_key"]): row for row in existing_rows if safe_text(row["entry_key"])}
        by_position = {int(row["position"]): row for row in existing_rows}
        kept: set[int] = set()

        for position, raw_item in enumerate(report.get("equipment") or [], start=1):
            if not isinstance(raw_item, dict):
                continue
            item = dict(raw_item)
            entry_key = safe_text(item.get("entryId")) or f"legacy-{position}"
            existing = by_entry.get(entry_key)
            if existing is None:
                candidate = by_position.get(position)
                if candidate is not None and int(candidate["id"]) not in kept:
                    existing = candidate

            attended_type = safe_text(item.get("attendedType") or item.get("equipmentType")) or "MD400"
            equipment_id = core.upsert_equipment(conn, item)
            snapshot = dict(item)
            snapshot["beforeImage"] = ""
            snapshot["afterImage"] = ""
            snapshot.pop("beforeImageRemoved", None)
            snapshot.pop("afterImageRemoved", None)
            values = (
                equipment_id,
                position,
                entry_key,
                safe_text(item.get("ip")),
                attended_type,
                safe_text(item.get("title")) or f"Equipamento {position:02d}",
                json.dumps(snapshot, ensure_ascii=False),
                safe_text(item.get("service")),
                safe_text(item.get("status")) or "Operacional",
                safe_text(item.get("notes")),
            )

            if existing is not None:
                item_id = int(existing["id"])
                conn.execute(
                    "UPDATE report_items SET entry_key='' WHERE report_id=? AND entry_key=? AND id<>?",
                    (report_id, entry_key, item_id),
                )
                conn.execute(
                    """
                    UPDATE report_items SET
                      equipment_id=?,position=?,entry_key=?,device_ip=?,attended_type=?,title=?,
                      snapshot_json=?,service=?,status=?,notes=?,updated_at=CURRENT_TIMESTAMP
                    WHERE id=?
                    """,
                    (*values, item_id),
                )
            else:
                row = conn.execute(
                    "SELECT id FROM report_items WHERE report_id=? AND entry_key=?", (report_id, entry_key)
                ).fetchone()
                if row:
                    item_id = int(row["id"])
                    conn.execute(
                        """
                        UPDATE report_items SET
                          equipment_id=?,position=?,device_ip=?,attended_type=?,title=?,snapshot_json=?,
                          service=?,status=?,notes=?,updated_at=CURRENT_TIMESTAMP
                        WHERE id=?
                        """,
                        (values[0], values[1], values[3], values[4], values[5], values[6], values[7], values[8], values[9], item_id),
                    )
                else:
                    cur = conn.execute(
                        """
                        INSERT INTO report_items(
                          report_id,equipment_id,position,entry_key,device_ip,attended_type,title,
                          snapshot_json,service,status,notes
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (report_id, *values),
                    )
                    item_id = int(cur.lastrowid)

            kept.add(item_id)
            for field, kind, column in (
                ("beforeImage", "before", "before_image_id"),
                ("afterImage", "after", "after_image_id"),
            ):
                path, warning = update_image(conn, day_key, item, item_id, equipment_id, field, kind, column)
                if path:
                    saved_images.append(path)
                if warning:
                    warnings.append(f"item {position}: {warning}")

        if mode == "replace":
            for row in existing_rows:
                if int(row["id"]) not in kept:
                    conn.execute("DELETE FROM report_items WHERE id=?", (row["id"],))

        revision = current_revision + 1
        digest = content_hash(report_hash_payload(report))
        conn.execute(
            "UPDATE reports SET revision=?,content_hash=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (revision, digest, report_id),
        )
        conn.commit()

    return {
        "ok": True,
        "dayKey": day_key,
        "revision": revision,
        "contentHash": digest,
        "savedImages": saved_images,
        "warnings": warnings,
    }


def load_report(day_key: str) -> dict | None:
    report = core.load_legacy_report(day_key)
    if not report:
        return None
    with connect() as conn:
        row = conn.execute("SELECT revision,content_hash FROM reports WHERE day_key=?", (day_key,)).fetchone()
    report["revision"] = int(row["revision"] or 0) if row else 0
    report["contentHash"] = safe_text(row["content_hash"]) if row else ""
    return report


def load_reports() -> list[dict]:
    with connect() as conn:
        days = [row["day_key"] for row in conn.execute("SELECT day_key FROM reports ORDER BY day_key")]
    return [report for day in days if (report := load_report(day))]


def normalized_devices(devices: list[dict]) -> list[dict]:
    result: list[dict] = []
    positions: dict[str, int] = {}
    for index, raw in enumerate(devices, start=1):
        if not isinstance(raw, dict):
            continue
        device_ip = safe_text(raw.get("ip"))
        if not device_ip:
            continue
        key = normalize_ip(device_ip)
        device = dict(raw)
        device["ip"] = device_ip
        device["number"] = device.get("number") or index
        if key in positions:
            current = result[positions[key]]
            for field, value in device.items():
                if not safe_text(current.get(field)) and safe_text(value):
                    current[field] = value
        else:
            positions[key] = len(result)
            result.append(device)
    return result


def device_state(conn: sqlite3.Connection | None = None) -> dict:
    owns = conn is None
    conn = conn or connect()
    try:
        row = conn.execute("SELECT revision,content_hash,updated_at FROM device_sync WHERE id=1").fetchone()
        count = int(conn.execute("SELECT COUNT(*) AS c FROM device_base").fetchone()["c"])
        return {
            "revision": int(row["revision"] or 0),
            "contentHash": safe_text(row["content_hash"]),
            "updatedAt": safe_text(row["updated_at"]),
            "count": count,
        }
    finally:
        if owns:
            conn.close()


def save_devices(devices: list[dict], mode: str = "merge", expected_revision=None) -> dict:
    incoming = normalized_devices(devices)
    with _DEVICE_LOCK, connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        state = device_state(conn)
        if expected_revision is not None and int(expected_revision) != state["revision"]:
            conn.rollback()
            raise RevisionConflict(state["revision"])

        if mode == "merge":
            current = normalized_devices(core.load_devices())
            by_ip = {normalize_ip(item.get("ip")): item for item in current}
            for device in incoming:
                key = normalize_ip(device.get("ip"))
                if key in by_ip:
                    by_ip[key].update({k: v for k, v in device.items() if safe_text(v)})
                else:
                    by_ip[key] = device
            incoming = list(by_ip.values())

        conn.execute("DELETE FROM device_base")
        for position, device in enumerate(incoming, start=1):
            conn.execute(
                "INSERT INTO device_base(ip,position,payload_json) VALUES(?,?,?)",
                (safe_text(device["ip"]), position, json.dumps(device, ensure_ascii=False)),
            )
        revision = state["revision"] + 1
        digest = content_hash(incoming)
        conn.execute(
            "UPDATE device_sync SET revision=?,content_hash=?,updated_at=CURRENT_TIMESTAMP WHERE id=1",
            (revision, digest),
        )
        conn.commit()
    return {"ok": True, "count": len(incoming), "revision": revision, "contentHash": digest, "mode": mode}


def portable_report(report: dict) -> dict:
    copy = json.loads(json.dumps(report, ensure_ascii=False))
    for item in copy.get("equipment") or []:
        for field in ("beforeImage", "afterImage"):
            value = safe_text(item.get(field))
            match = core.IMAGE_URL_RE.match(value)
            if not match:
                continue
            target = (IMAGE_DIR / unquote(match.group(1))).resolve()
            if target.exists() and IMAGE_DIR.resolve() in target.parents:
                mime = mimetypes.guess_type(target.name)[0] or "image/jpeg"
                item[field] = f"data:{mime};base64," + base64.b64encode(target.read_bytes()).decode("ascii")
    return copy


def export_payload() -> dict:
    reports = [portable_report(report) for report in load_reports()]
    return {
        "version": 4,
        "exportedAt": utc_now(),
        "source": "server",
        "devices": core.load_devices(),
        "reports": reports,
        "state": system_state(),
    }


def write_snapshot() -> str:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    path = SNAPSHOT_DIR / f"before-restore-{stamp}.json"
    path.write_text(json.dumps(export_payload(), ensure_ascii=False), encoding="utf-8")
    return str(path.relative_to(DATA_DIR))


def restore_payload(payload: dict, mode: str = "merge") -> dict:
    if not isinstance(payload, dict):
        raise ValueError("backup inválido")
    reports = payload.get("reports") or []
    devices = payload.get("devices") or []
    if not isinstance(reports, list) or not isinstance(devices, list):
        raise ValueError("backup inválido")
    snapshot = None
    with _RESTORE_LOCK:
        if mode == "replace":
            snapshot = write_snapshot()
            with connect() as conn:
                conn.execute("DELETE FROM reports")
                conn.commit()
        device_result = save_devices(devices, mode=mode)
        imported = []
        errors = []
        for report in reports:
            if not isinstance(report, dict):
                continue
            try:
                imported.append(save_report(report, mode=mode))
            except Exception as error:
                errors.append({"dayKey": report.get("dayKey"), "error": str(error)})
    return {
        "ok": not errors,
        "mode": mode,
        "snapshot": snapshot,
        "devices": device_result,
        "reportsImported": len(imported),
        "errors": errors,
    }


def system_state() -> dict:
    with connect() as conn:
        device = device_state(conn)
        report_count = int(conn.execute("SELECT COUNT(*) AS c FROM reports").fetchone()["c"])
        item_count = int(conn.execute("SELECT COUNT(*) AS c FROM report_items").fetchone()["c"])
        image_count = int(conn.execute("SELECT COUNT(*) AS c FROM images").fetchone()["c"])
        max_updated = conn.execute("SELECT MAX(updated_at) AS value FROM reports").fetchone()["value"]
    return {
        "devices": device,
        "reports": {"count": report_count, "items": item_count, "images": image_count, "updatedAt": max_updated or ""},
    }


class Handler(core.SiteHandler):
    def serve_index_with_hardening(self) -> None:
        path = ROOT / "index.html"
        if not path.exists():
            return self.send_error(404)
        text = path.read_text(encoding="utf-8-sig")
        bootstrap = '<script src="/logic-bootstrap.js?v=4"></script>'
        scripts = '<script src="/logic-fixes.js?v=4"></script>\n<script src="/sync-hardening.js?v=4"></script>'
        if bootstrap not in text:
            text = text.replace("<script>", bootstrap + "\n<script>", 1)
        if scripts not in text:
            text = text.replace("</body>", scripts + "\n</body>")
        data = text.encode("utf-8")
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError):
            return

    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        try:
            if path in ("/", "/index.html"):
                return self.serve_index_with_hardening()
            if path == "/api/state":
                return safe_json_response(self, 200, {"ok": True, **system_state()})
            if path == "/api/export":
                return safe_json_response(self, 200, {"ok": True, "backup": export_payload()})
            if path == "/api/report":
                day_key = core.safe_day_key(parse_qs(parsed.query).get("dayKey", [""])[0])
                report = load_report(day_key)
                if not report:
                    return safe_json_response(self, 404, {"ok": False, "error": "Relatório não encontrado"})
                return safe_json_response(self, 200, {"ok": True, "report": report})
            if path == "/api/reports":
                return safe_json_response(self, 200, {"ok": True, "reports": load_reports()})
            if path == "/api/devices":
                return safe_json_response(self, 200, {"ok": True, "devices": core.load_devices(), **device_state()})
            return super().do_GET()
        except Exception as error:
            traceback.print_exc()
            return safe_json_response(self, 400, {"ok": False, "error": str(error)})

    def do_POST(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        try:
            payload = core.read_json_body(self) if path in {"/api/report", "/api/devices", "/api/restore"} else None
            if path == "/api/report":
                mode = safe_text(payload.get("syncMode")) or "replace"
                return safe_json_response(self, 200, save_report(payload, mode=mode))
            if path == "/api/devices":
                devices = payload.get("devices") if isinstance(payload, dict) else None
                if not isinstance(devices, list):
                    return safe_json_response(self, 400, {"ok": False, "error": "devices inválido"})
                mode = safe_text(payload.get("mode")) or "merge"
                result = save_devices(devices, mode=mode, expected_revision=payload.get("expectedRevision"))
                return safe_json_response(self, 200, result)
            if path == "/api/restore":
                mode = safe_text(payload.get("mode")) or "merge"
                backup = payload.get("backup") if isinstance(payload, dict) else None
                return safe_json_response(self, 200, restore_payload(backup, mode=mode))
            return super().do_POST()
        except RevisionConflict as error:
            return safe_json_response(
                self,
                409,
                {"ok": False, "error": "VERSION_CONFLICT", "message": str(error), "currentRevision": error.current_revision},
            )
        except Exception as error:
            traceback.print_exc()
            return safe_json_response(self, 400, {"ok": False, "error": str(error)})


def main() -> None:
    init_db()
    core.connect = connect
    core.json_response = safe_json_response
    core.store_image_bytes = contextual_store_image
    core.save_legacy_report = save_report
    core.save_devices = save_devices
    port = int(os.environ.get("PORT", "8880"))
    host = os.environ.get("HOST", "127.0.0.1")
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"YARA report system protegido em http://{host}:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
