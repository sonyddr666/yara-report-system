from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import re
import sqlite3
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse


ROOT = Path(__file__).resolve().parent
LEGACY_ROOT = ROOT.parent
DATA_DIR = ROOT / "data"
IMAGE_DIR = DATA_DIR / "images"
DB_PATH = ROOT / "database.db"
SCHEMA_PATH = ROOT / "schema.sql"
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DATA_URL_RE = re.compile(r"^data:(image/[a-zA-Z0-9.+-]+);base64,(.*)$", re.DOTALL)
IMAGE_URL_RE = re.compile(r"^/?images/(.+)$")
EXT_BY_MIME = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))


def safe_day_key(value: str) -> str:
    value = str(value or "").strip()
    if not DAY_RE.match(value):
        raise ValueError("dayKey invalido")
    return value


def safe_text(value) -> str:
    return str(value or "").strip()


def br_to_iso(value: str) -> str:
    parts = safe_text(value).split("/")
    if len(parts) == 3:
        return safe_day_key(f"{parts[2]}-{parts[1]}-{parts[0]}")
    return safe_day_key(value)


def json_response(handler, status: int, payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def read_json_body(handler) -> dict:
    length = int(handler.headers.get("Content-Length") or 0)
    raw = handler.rfile.read(length)
    return json.loads(raw.decode("utf-8") or "{}") if raw else {}


def read_multipart_file(handler) -> bytes | None:
    content_type = handler.headers.get("Content-Type", "")
    match = re.search(r"boundary=([^;]+)", content_type)
    if not match:
        return None
    boundary = ("--" + match.group(1).strip().strip('"')).encode("utf-8")
    length = int(handler.headers.get("Content-Length") or 0)
    body = handler.rfile.read(length)
    for part in body.split(boundary):
        if b'name="file"' not in part or b"\r\n\r\n" not in part:
            continue
        _, content = part.split(b"\r\n\r\n", 1)
        return content.rstrip(b"\r\n-")
    return None


def equipment_payload(item: dict) -> dict:
    return {
        "name": safe_text(item.get("location") or item.get("title") or "Equipamento"),
        "equipment_type": safe_text(item.get("equipmentType")),
        "location": safe_text(item.get("location")),
        "area": safe_text(item.get("area")),
        "ip": safe_text(item.get("ip")),
        "mac": safe_text(item.get("mac")),
        "firmware": safe_text(item.get("fw")),
        "code": safe_text(item.get("codigo")),
        "serial": safe_text(item.get("serial")),
        "notes": safe_text(item.get("notes")),
    }


def upsert_equipment(conn: sqlite3.Connection, item: dict) -> int:
    payload = equipment_payload(item)
    existing = conn.execute(
        """
        SELECT id FROM equipments
        WHERE name = ? AND location = ? AND ip = ? AND serial = ?
        """,
        (payload["name"], payload["location"], payload["ip"], payload["serial"]),
    ).fetchone()
    if existing:
        conn.execute(
            """
            UPDATE equipments
            SET equipment_type = COALESCE(NULLIF(?, ''), equipment_type),
                area = COALESCE(NULLIF(?, ''), area),
                mac = COALESCE(NULLIF(?, ''), mac),
                firmware = COALESCE(NULLIF(?, ''), firmware),
                code = COALESCE(NULLIF(?, ''), code),
                notes = COALESCE(NULLIF(?, ''), notes),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                payload["equipment_type"], payload["area"], payload["mac"],
                payload["firmware"], payload["code"], payload["notes"], existing["id"]
            ),
        )
        return int(existing["id"])
    cur = conn.execute(
        """
        INSERT INTO equipments
        (name, equipment_type, location, area, ip, mac, firmware, code, serial, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        tuple(payload.values()),
    )
    return int(cur.lastrowid)


def upsert_report(conn: sqlite3.Connection, report: dict) -> int:
    day_key = safe_day_key(report.get("dayKey") or br_to_iso(report.get("header", {}).get("dataRelatorio")))
    header = report.get("header") or {}
    title = safe_text(header.get("title")) or "RELATÓRIO FOTOGRÁFICO — MANUTENÇÃO PREVENTIVA DIÁRIA"
    company = safe_text(header.get("empresa")) or "YARA"
    general_location = safe_text(header.get("localGeral")) or "RIG1"
    summary = header.get("summary") or []
    status = safe_text(summary[2] if len(summary) > 2 else "Concluído")
    notes = safe_text(header.get("footer"))
    existing = conn.execute("SELECT id FROM reports WHERE day_key = ?", (day_key,)).fetchone()
    if existing:
        conn.execute(
            """
            UPDATE reports
            SET title = ?, company = ?, general_location = ?, status = ?, notes = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (title, company, general_location, status, notes, existing["id"]),
        )
        return int(existing["id"])
    cur = conn.execute(
        """
        INSERT INTO reports (day_key, title, company, general_location, status, notes)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (day_key, title, company, general_location, status, notes),
    )
    return int(cur.lastrowid)


def image_bytes_from_value(value: str) -> tuple[bytes, str, str] | None:
    value = safe_text(value)
    match = DATA_URL_RE.match(value)
    if match:
        mime_type, encoded = match.groups()
        return base64.b64decode(encoded, validate=False), mime_type, ""
    match = IMAGE_URL_RE.match(value)
    if match:
        rel = unquote(match.group(1)).replace("/", os.sep)
        path = (IMAGE_DIR / rel.removeprefix("images" + os.sep)).resolve()
        if DATA_DIR.resolve() not in path.parents or not path.exists():
            return None
        return path.read_bytes(), mimetypes.guess_type(path.name)[0] or "image/jpeg", path.name
    return None


def store_image_bytes(
    conn: sqlite3.Connection,
    day_key: str,
    equipment_id: int | None,
    kind: str,
    content: bytes,
    mime_type: str,
    original_name: str = "",
) -> tuple[int, bool]:
    digest = hashlib.sha256(content).hexdigest()
    existing = conn.execute("SELECT id FROM images WHERE sha256 = ?", (digest,)).fetchone()
    if existing:
        return int(existing["id"]), True

    day_dir = IMAGE_DIR / day_key
    day_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(original_name).suffix.lower() if original_name and Path(original_name).suffix else EXT_BY_MIME.get(mime_type.lower(), ".jpg")
    filename = f"equipment-{equipment_id or 'unknown'}-{kind}-{digest[:12]}{ext}"
    target = day_dir / filename
    target.write_bytes(content)
    rel_path = target.relative_to(DATA_DIR).as_posix()
    cur = conn.execute(
        """
        INSERT INTO images (day_key, equipment_id, kind, original_name, file_path, sha256, mime_type, size_bytes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (day_key, equipment_id, kind, original_name, rel_path, digest, mime_type, len(content)),
    )
    return int(cur.lastrowid), False


def attach_legacy_images(conn: sqlite3.Connection, day_key: str, index: int, item: dict, item_id: int, equipment_id: int) -> list[str]:
    saved = []
    for field, kind, column in (("beforeImage", "before", "before_image_id"), ("afterImage", "after", "after_image_id")):
        value = item.get(field) or ""
        image_data = image_bytes_from_value(value)
        if image_data is None:
            conn.execute(f"UPDATE report_items SET {column} = NULL WHERE id = ?", (item_id,))
            continue
        image_id, reused = store_image_bytes(conn, day_key, equipment_id, kind, image_data[0], image_data[1], image_data[2] or f"equipamento-{index:02d}-{field}.jpg")
        conn.execute(f"UPDATE report_items SET {column} = ? WHERE id = ?", (image_id, item_id))
        if not reused:
            file_path = conn.execute("SELECT file_path FROM images WHERE id = ?", (image_id,)).fetchone()["file_path"]
            saved.append(file_path)
    return saved


def save_legacy_report(report: dict) -> dict:
    day_key = safe_day_key(report.get("dayKey") or br_to_iso(report.get("header", {}).get("dataRelatorio")))
    report["dayKey"] = day_key
    saved_images = []
    with connect() as conn:
        report_id = upsert_report(conn, report)
        conn.execute("DELETE FROM report_items WHERE report_id = ?", (report_id,))
        for index, item in enumerate(report.get("equipment") or [], start=1):
            equipment_id = upsert_equipment(conn, item)
            snapshot = dict(item)
            snapshot["beforeImage"] = ""
            snapshot["afterImage"] = ""
            cur = conn.execute(
                """
                INSERT INTO report_items
                (report_id, equipment_id, position, title, snapshot_json, service, status, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report_id,
                    equipment_id,
                    index,
                    safe_text(item.get("title")) or f"Equipamento {index:02d}",
                    json.dumps(snapshot, ensure_ascii=False),
                    safe_text(item.get("service")),
                    safe_text(item.get("status")) or "Operacional",
                    safe_text(item.get("notes")),
                ),
            )
            saved_images.extend(attach_legacy_images(conn, day_key, index, item, int(cur.lastrowid), equipment_id))
        conn.commit()
    return {"ok": True, "dayKey": day_key, "savedImages": saved_images}


def image_url(row: sqlite3.Row | None) -> str:
    if not row:
        return ""
    return "/images/" + str(row["file_path"]).removeprefix("images/")


def load_legacy_report(day_key: str) -> dict | None:
    day_key = safe_day_key(day_key)
    with connect() as conn:
        report_row = conn.execute("SELECT * FROM reports WHERE day_key = ?", (day_key,)).fetchone()
        if not report_row:
            return None
        item_rows = conn.execute(
            """
            SELECT ri.*, bi.file_path AS before_path, ai.file_path AS after_path
            FROM report_items ri
            LEFT JOIN images bi ON bi.id = ri.before_image_id
            LEFT JOIN images ai ON ai.id = ri.after_image_id
            WHERE ri.report_id = ?
            ORDER BY ri.position, ri.id
            """,
            (report_row["id"],),
        ).fetchall()

    equipment = []
    for row in item_rows:
        try:
            item = json.loads(row["snapshot_json"] or "{}")
        except json.JSONDecodeError:
            item = {}
        item["title"] = item.get("title") or row["title"]
        item["service"] = item.get("service") or row["service"] or ""
        item["status"] = item.get("status") or row["status"] or ""
        item["notes"] = item.get("notes") or row["notes"] or ""
        item["beforeImage"] = image_url({"file_path": row["before_path"]} if row["before_path"] else None)
        item["afterImage"] = image_url({"file_path": row["after_path"]} if row["after_path"] else None)
        equipment.append(item)

    date_br = "/".join(reversed(day_key.split("-")))
    header = {
        "title": report_row["title"],
        "badge": "DIÁRIO",
        "empresa": report_row["company"],
        "localGeral": report_row["general_location"],
        "dataRelatorio": date_br,
        "metaValues": [report_row["company"], report_row["general_location"], "Manutenção preventiva diária", "Fotográfico técnico", date_br],
        "summary": ["Registro fotográfico diário das atividades de manutenção preventiva realizadas nos equipamentos listados.", str(len(equipment)), report_row["status"]],
        "footer": report_row["notes"] or "",
    }
    return {"dayKey": day_key, "updatedAt": report_row["updated_at"], "header": header, "equipment": equipment}


def load_all_legacy_reports() -> list[dict]:
    with connect() as conn:
        days = [row["day_key"] for row in conn.execute("SELECT day_key FROM reports ORDER BY day_key").fetchall()]
    return [report for day in days if (report := load_legacy_report(day))]


def import_report_object(report: dict, old_data_dir: Path | None = None) -> dict:
    day_key = safe_day_key(report.get("dayKey") or br_to_iso(report.get("header", {}).get("dataRelatorio")))
    if old_data_dir:
        legacy_dir = old_data_dir / "images" / day_key
        for index, item in enumerate(report.get("equipment") or [], start=1):
            for field in ("beforeImage", "afterImage"):
                if item.get(field):
                    continue
                matches = list(legacy_dir.glob(f"equipamento-{index:02d}-{field}.*"))
                if not matches:
                    continue
                mime_type = mimetypes.guess_type(matches[0].name)[0] or "image/jpeg"
                item[field] = f"data:{mime_type};base64," + base64.b64encode(matches[0].read_bytes()).decode("ascii")
    before = count_images()
    result = save_legacy_report(report)
    after = count_images()
    return {
        "day_key": result["dayKey"],
        "items": len(report.get("equipment") or []),
        "images": after - before,
        "reused_images": max(0, len([img for item in report.get("equipment") or [] for img in (item.get("beforeImage"), item.get("afterImage")) if img]) - (after - before)),
        "warnings": [],
    }


def count_images() -> int:
    with connect() as conn:
        return int(conn.execute("SELECT COUNT(*) AS c FROM images").fetchone()["c"])


class SiteHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(LEGACY_ROOT), **kwargs)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        try:
            if path == "/api/report":
                day_key = safe_day_key(parse_qs(parsed.query).get("dayKey", [""])[0])
                report = load_legacy_report(day_key)
                if not report:
                    return json_response(self, 404, {"ok": False, "error": "Relatorio nao encontrado"})
                return json_response(self, 200, {"ok": True, "report": report})
            if path == "/api/reports":
                return json_response(self, 200, {"ok": True, "reports": load_all_legacy_reports()})
            if path.startswith("/images/"):
                rel = path.removeprefix("/images/")
                target = (IMAGE_DIR / rel).resolve()
                if DATA_DIR.resolve() not in target.parents:
                    return self.send_error(403)
                return self.serve_file(target)
        except Exception as error:
            return json_response(self, 400, {"ok": False, "error": str(error)})
        return super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        try:
            if path == "/api/report":
                return json_response(self, 200, save_legacy_report(read_json_body(self)))
            if path == "/api/import-json":
                file_bytes = read_multipart_file(self)
                if not file_bytes:
                    return json_response(self, 400, {"ok": False, "error": "arquivo ausente"})
                report = json.loads(file_bytes.decode("utf-8"))
                return json_response(self, 200, {"ok": True, **import_report_object(report, LEGACY_ROOT / "data")})
        except Exception as error:
            return json_response(self, 400, {"ok": False, "error": str(error)})
        return json_response(self, 404, {"ok": False, "error": "Rota nao encontrada"})

    def do_DELETE(self):
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/report":
                day_key = safe_day_key(parse_qs(parsed.query).get("dayKey", [""])[0])
                with connect() as conn:
                    conn.execute("DELETE FROM reports WHERE day_key = ?", (day_key,))
                    conn.commit()
                return json_response(self, 200, {"ok": True, "dayKey": day_key})
        except Exception as error:
            return json_response(self, 400, {"ok": False, "error": str(error)})
        return json_response(self, 404, {"ok": False, "error": "Rota nao encontrada"})

    def serve_file(self, path: Path):
        if not path.exists() or not path.is_file():
            return self.send_error(404)
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> None:
    init_db()
    port = int(os.environ.get("PORT", "8890"))
    host = os.environ.get("HOST", "127.0.0.1")
    server = ThreadingHTTPServer((host, port), SiteHandler)
    print(f"YARA report system novo em http://{host}:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
