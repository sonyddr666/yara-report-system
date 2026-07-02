from __future__ import annotations

import json
import os
import re
import sqlite3
import uuid
from datetime import datetime
from http.server import ThreadingHTTPServer
from pathlib import Path

import server_core as core
import server_hardening as base

ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("YARA_DATA_DIR", str(ROOT / "data"))).expanduser().resolve()
SNAPSHOT_DIR = DATA_DIR / "snapshots"
PLACEHOLDER_IMAGES = {"[image_removed]", "[imagem_removida]", "[removed]", "null", "none", "undefined"}
ORIGINAL_RESTORE = getattr(base, "restore_payload", None) or getattr(base, "restore_backup")


def text(value) -> str:
    return str(value or "").strip()


def normalize_ip(value) -> str:
    return text(value).lower()


def safe_upsert_equipment(conn: sqlite3.Connection, item: dict) -> int:
    """Identifica equipamento pelo IP. MD410 nunca cria um cadastro separado."""
    payload = core.equipment_payload(item)
    ip = text(payload.get("ip"))
    existing = None
    if ip:
        existing = conn.execute(
            "SELECT id FROM equipments WHERE lower(trim(ip))=? ORDER BY id LIMIT 1",
            (normalize_ip(ip),),
        ).fetchone()
    if not existing:
        existing = conn.execute(
            """SELECT id FROM equipments
               WHERE name=? AND location=? AND serial=?
               ORDER BY id LIMIT 1""",
            (payload["name"], payload["location"], payload["serial"]),
        ).fetchone()

    values = (
        payload["name"], payload["equipment_type"], payload["location"], payload["area"],
        ip, payload["mac"], payload["firmware"], payload["code"], payload["serial"], payload["notes"],
    )
    if existing:
        conn.execute(
            """UPDATE equipments SET
                 name=COALESCE(NULLIF(?,''),name),
                 equipment_type=COALESCE(NULLIF(?,''),equipment_type),
                 location=COALESCE(NULLIF(?,''),location),
                 area=COALESCE(NULLIF(?,''),area),
                 ip=COALESCE(NULLIF(?,''),ip),
                 mac=COALESCE(NULLIF(?,''),mac),
                 firmware=COALESCE(NULLIF(?,''),firmware),
                 code=COALESCE(NULLIF(?,''),code),
                 serial=COALESCE(NULLIF(?,''),serial),
                 notes=COALESCE(NULLIF(?,''),notes),
                 updated_at=CURRENT_TIMESTAMP
               WHERE id=?""",
            (*values, existing["id"]),
        )
        return int(existing["id"])

    cur = conn.execute(
        """INSERT INTO equipments
           (name,equipment_type,location,area,ip,mac,firmware,code,serial,notes)
           VALUES(?,?,?,?,?,?,?,?,?,?)""",
        values,
    )
    return int(cur.lastrowid)


def clean_backup(backup: dict) -> tuple[dict, list[str]]:
    if not isinstance(backup, dict):
        raise ValueError("Backup inválido.")
    devices = backup.get("devices")
    reports = backup.get("reports")
    if not isinstance(devices, list) or not isinstance(reports, list):
        raise ValueError("O backup precisa conter devices e reports.")

    fixed = json.loads(json.dumps(backup, ensure_ascii=False))
    warnings: list[str] = []
    seen_days: set[str] = set()
    for report in fixed["reports"]:
        if not isinstance(report, dict) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text(report.get("dayKey"))):
            raise ValueError("Existe relatório sem data válida.")
        day = report["dayKey"]
        if day in seen_days:
            raise ValueError(f"O backup contém o dia {day} mais de uma vez.")
        seen_days.add(day)
        if not isinstance(report.get("header"), dict) or not isinstance(report.get("equipment"), list):
            raise ValueError(f"Relatório {day} inválido.")
        used: set[str] = set()
        for position, item in enumerate(report["equipment"], start=1):
            if not isinstance(item, dict):
                raise ValueError(f"Relatório {day}, item {position} inválido.")
            entry = re.sub(r"[^A-Za-z0-9_.:-]+", "-", text(item.get("entryId"))).strip("-")
            if not entry:
                entry = f"legacy-{position:03d}-{uuid.uuid4().hex[:8]}"
                warnings.append(f"{day}: item {position} recebeu ID estável.")
            base_entry = entry
            suffix = 2
            while entry in used:
                entry = f"{base_entry}-{suffix}"
                suffix += 1
            used.add(entry)
            item["entryId"] = entry
            item["attendedType"] = text(item.get("attendedType") or item.get("equipmentType"))
            for field in ("beforeImage", "afterImage"):
                if text(item.get(field)).lower() in PLACEHOLDER_IMAGES:
                    item[field] = ""
    return fixed, warnings


def export_current() -> dict:
    fn = getattr(base, "export_payload", None) or getattr(base, "export_backup")
    try:
        return fn()
    except TypeError:
        return fn(portable=True)


def safe_restore(backup: dict, mode: str = "merge") -> dict:
    fixed, warnings = clean_backup(backup)
    before = export_current()
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    snapshot_path = SNAPSHOT_DIR / f"before-import-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.json"
    snapshot_path.write_text(json.dumps(before, ensure_ascii=False), encoding="utf-8")
    try:
        result = ORIGINAL_RESTORE(fixed, mode=mode)
    except Exception:
        try:
            ORIGINAL_RESTORE(before, mode="replace")
        except Exception:
            pass
        raise
    result = dict(result or {})
    result["ok"] = True
    result["snapshot"] = str(snapshot_path.relative_to(DATA_DIR))
    result["warnings"] = list(result.get("warnings") or []) + warnings
    result.setdefault("reportsImported", len(fixed["reports"]))
    result.setdefault("devicesSaved", len(fixed["devices"]))
    return result


class Handler(base.Handler):
    def serve_index_with_hardening(self) -> None:
        index_path = ROOT / "index.html"
        if not index_path.is_file():
            self.send_error(404)
            return
        html = index_path.read_text(encoding="utf-8-sig")
        bootstrap = '<script src="/logic-bootstrap.js?v=8"></script>'
        after = (
            '<script src="/logic-fixes.js?v=8"></script>\n'
            '<script src="/sync-hardening.js?v=8"></script>\n'
            '<script src="/final-fixes.js?v=8"></script>'
        )
        html = html.replace("<script>", bootstrap + "\n<script>", 1)
        html = html.replace("</body>", after + "\n</body>")
        data = html.encode("utf-8")
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError):
            return

    serve_index_with_logic_patch = serve_index_with_hardening


def apply_patches() -> None:
    core.upsert_equipment = safe_upsert_equipment
    if hasattr(base, "connect"):
        core.connect = base.connect
    if hasattr(base, "safe_json_response"):
        core.json_response = base.safe_json_response
    if hasattr(base, "contextual_store_image"):
        core.store_image_bytes = base.contextual_store_image
    if hasattr(base, "save_report"):
        core.save_legacy_report = base.save_report
    if hasattr(base, "save_devices"):
        core.save_devices = base.save_devices
    if hasattr(base, "restore_payload"):
        base.restore_payload = safe_restore
    if hasattr(base, "restore_backup"):
        base.restore_backup = safe_restore


init_db = base.init_db
save_devices = base.save_devices
load_devices = base.load_devices
import_report_object = getattr(base, "import_report_object", None)
restore_payload = safe_restore
restore_backup = safe_restore


def main() -> None:
    apply_patches()
    base.init_db()
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8880"))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"YARA report system em http://{host}:{port}", flush=True)
    print(f"Dados persistentes: {DATA_DIR}", flush=True)
    server.serve_forever()
