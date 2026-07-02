from __future__ import annotations

import json
import os
from datetime import datetime
from http.server import ThreadingHTTPServer
from pathlib import Path

import server_core as core
import server_hardening as base

ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("YARA_DATA_DIR", str(ROOT / "data"))).expanduser().resolve()
IMAGE_DIR = DATA_DIR / "images"
SNAPSHOT_DIR = DATA_DIR / "snapshots"
DB_PATH = DATA_DIR / "database.db"

for module in (core, base):
    module.DATA_DIR = DATA_DIR
    module.IMAGE_DIR = IMAGE_DIR
    module.DB_PATH = DB_PATH
if hasattr(base, "SNAPSHOT_DIR"):
    base.SNAPSHOT_DIR = SNAPSHOT_DIR

import server_final as final


def repair_text(value: str) -> str:
    if not isinstance(value, str) or not any(mark in value for mark in ("Ã", "Â", "â", "ð", "�")):
        return value
    markers = ("Ã", "Â", "â", "ð", "�")
    original_score = sum(value.count(mark) for mark in markers)
    candidates = []
    for encoding in ("cp1252", "latin-1"):
        try:
            candidates.append(value.encode(encoding).decode("utf-8"))
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    if not candidates:
        return value
    best = min(candidates, key=lambda item: sum(item.count(mark) for mark in markers))
    best_score = sum(best.count(mark) for mark in markers)
    return best if best_score < original_score else value


def repair_value(value):
    if isinstance(value, str):
        return repair_text(value)
    if isinstance(value, list):
        return [repair_value(item) for item in value]
    if isinstance(value, dict):
        return {key: repair_value(item) for key, item in value.items()}
    return value


def runtime_upsert_equipment(conn, item: dict) -> int:
    device_ip = str(item.get("ip") or "").strip().lower()
    attended_type = str(item.get("attendedType") or item.get("equipmentType") or "").strip().upper()
    if device_ip:
        existing = conn.execute(
            "SELECT id FROM equipments WHERE lower(trim(ip))=? ORDER BY id LIMIT 1",
            (device_ip,),
        ).fetchone()
        if existing and "MD410" in attended_type:
            return int(existing["id"])
    return final.safe_upsert_equipment(conn, item)


def transactional_restore(backup: dict, mode: str = "merge") -> dict:
    fixed, warnings = final.clean_backup(repair_value(backup))
    before = final.export_current()
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    snapshot_path = SNAPSHOT_DIR / f"before-import-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.json"
    snapshot_path.write_text(json.dumps(before, ensure_ascii=False), encoding="utf-8")

    try:
        result = dict(final.ORIGINAL_RESTORE(fixed, mode=mode) or {})
        errors = list(result.get("errors") or [])
        if errors or result.get("ok") is False:
            failed_days = ", ".join(str(item.get("dayKey") or "sem data") for item in errors[:10])
            raise RuntimeError(f"Falha em {len(errors)} relatório(s): {failed_days}".rstrip(": "))
    except Exception:
        try:
            final.ORIGINAL_RESTORE(before, mode="replace")
        except Exception:
            pass
        raise

    device_result = result.get("devices") if isinstance(result.get("devices"), dict) else {}
    result["ok"] = True
    result["snapshot"] = str(snapshot_path.relative_to(DATA_DIR))
    result["warnings"] = list(result.get("warnings") or []) + warnings
    result["reportsImported"] = int(result.get("reportsImported") or len(fixed["reports"]))
    result["devicesSaved"] = int(result.get("devicesSaved") or device_result.get("count") or len(fixed["devices"]))
    return result


def apply_runtime_patches() -> None:
    final.apply_patches()
    core.upsert_equipment = runtime_upsert_equipment
    base.restore_payload = transactional_restore
    if hasattr(base, "restore_backup"):
        base.restore_backup = transactional_restore
    final.restore_payload = transactional_restore
    final.restore_backup = transactional_restore


init_db = base.init_db
save_devices = base.save_devices
load_devices = base.load_devices
restore_payload = transactional_restore
restore_backup = transactional_restore
Handler = final.Handler


def main() -> None:
    apply_runtime_patches()
    base.init_db()
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8880"))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"YARA report system em http://{host}:{port}", flush=True)
    print(f"Dados persistentes: {DATA_DIR}", flush=True)
    server.serve_forever()
