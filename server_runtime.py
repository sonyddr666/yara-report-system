from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
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

RESTORE_LOCK = getattr(base, "_RESTORE_LOCK", threading.RLock())
ORIGINAL_SAVE_REPORT = base.save_report
ORIGINAL_SAVE_DEVICES = base.save_devices


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


def migrate_legacy_equipments() -> None:
    if not DB_PATH.exists():
        return
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='equipments'"
        ).fetchone()
        if not row:
            return
        normalized_sql = re.sub(r"\s+", "", str(row["sql"] or "").lower())
        if "unique(name,location,ip,serial)" not in normalized_sql:
            return

        table_names = {
            item["name"]
            for item in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DROP TABLE IF EXISTS equipments_clean")
        conn.execute(
            """
            CREATE TABLE equipments_clean (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              name TEXT NOT NULL,
              equipment_type TEXT DEFAULT '',
              location TEXT DEFAULT '',
              area TEXT DEFAULT '',
              ip TEXT DEFAULT '',
              mac TEXT DEFAULT '',
              firmware TEXT DEFAULT '',
              code TEXT DEFAULT '',
              serial TEXT DEFAULT '',
              notes TEXT DEFAULT '',
              active INTEGER NOT NULL DEFAULT 1,
              possui_md410 INTEGER NOT NULL DEFAULT 0,
              base_model TEXT NOT NULL DEFAULT 'md400',
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        mapping: dict[int, int] = {}
        by_ip: dict[str, int] = {}
        rows = conn.execute("SELECT * FROM equipments ORDER BY id").fetchall()
        for old in rows:
            old_keys = set(old.keys())
            old_id = int(old["id"])
            ip_value = str(old["ip"] or "").strip() if "ip" in old_keys else ""
            ip_key = ip_value.lower()
            if ip_key and ip_key in by_ip:
                mapping[old_id] = by_ip[ip_key]
                continue

            def value(name: str, default=""):
                return old[name] if name in old_keys and old[name] is not None else default

            cur = conn.execute(
                """
                INSERT INTO equipments_clean (
                  name,equipment_type,location,area,ip,mac,firmware,code,serial,notes,
                  active,possui_md410,base_model,created_at,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    str(value("name", "Equipamento")), str(value("equipment_type", "")),
                    str(value("location", "")), str(value("area", "")), ip_value,
                    str(value("mac", "")), str(value("firmware", "")), str(value("code", "")),
                    str(value("serial", "")), str(value("notes", "")), int(value("active", 1)),
                    int(value("possui_md410", 0)), str(value("base_model", "md400")),
                    str(value("created_at", datetime.now().isoformat())),
                    str(value("updated_at", datetime.now().isoformat())),
                ),
            )
            new_id = int(cur.lastrowid)
            mapping[old_id] = new_id
            if ip_key:
                by_ip[ip_key] = new_id

        if "report_items" in table_names:
            for old_id, new_id in mapping.items():
                conn.execute(
                    "UPDATE report_items SET equipment_id=? WHERE equipment_id=?",
                    (new_id, old_id),
                )
        if "images" in table_names:
            for old_id, new_id in mapping.items():
                conn.execute(
                    "UPDATE images SET equipment_id=? WHERE equipment_id=?",
                    (new_id, old_id),
                )

        conn.execute("DROP TABLE equipments")
        conn.execute("ALTER TABLE equipments_clean RENAME TO equipments")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_equipments_ip_unique "
            "ON equipments(lower(trim(ip))) WHERE trim(ip) <> ''"
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


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


def gated_save_report(report: dict, mode: str = "replace") -> dict:
    with RESTORE_LOCK:
        return ORIGINAL_SAVE_REPORT(report, mode=mode)


def gated_save_devices(devices, mode: str = "merge", expected_revision=None) -> dict:
    with RESTORE_LOCK:
        return ORIGINAL_SAVE_DEVICES(devices, mode=mode, expected_revision=expected_revision)


def cleanup_orphans() -> None:
    removable_paths: list[Path] = []
    with base.connect() as conn:
        rows = conn.execute(
            """
            SELECT id,file_path FROM images
            WHERE id NOT IN (
              SELECT before_image_id FROM report_items WHERE before_image_id IS NOT NULL
              UNION
              SELECT after_image_id FROM report_items WHERE after_image_id IS NOT NULL
            )
            """
        ).fetchall()
        for row in rows:
            relative = str(row["file_path"] or "")
            target = (DATA_DIR / relative).resolve()
            if relative and DATA_DIR in target.parents:
                removable_paths.append(target)
        conn.executemany("DELETE FROM images WHERE id=?", [(row["id"],) for row in rows])
        conn.execute(
            "DELETE FROM equipments WHERE id NOT IN (SELECT DISTINCT equipment_id FROM report_items)"
        )
        conn.commit()
    for target in removable_paths:
        try:
            target.unlink(missing_ok=True)
        except OSError:
            pass


def transactional_restore(backup: dict, mode: str = "merge") -> dict:
    mode = "replace" if mode == "replace" else "merge"
    fixed, warnings = final.clean_backup(repair_value(backup))
    before = final.export_current()
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    snapshot_path = SNAPSHOT_DIR / f"before-import-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.json"
    snapshot_path.write_text(json.dumps(before, ensure_ascii=False), encoding="utf-8")

    with RESTORE_LOCK:
        try:
            if mode == "replace":
                with base.connect() as conn:
                    conn.execute("DELETE FROM reports")
                    conn.commit()
            device_result = ORIGINAL_SAVE_DEVICES(fixed["devices"], mode=mode)
            imported = []
            errors = []
            for report in fixed["reports"]:
                try:
                    imported.append(ORIGINAL_SAVE_REPORT(report, mode="replace"))
                except Exception as error:
                    errors.append({"dayKey": report.get("dayKey"), "error": str(error)})
            if errors:
                failed_days = ", ".join(str(item.get("dayKey") or "sem data") for item in errors[:10])
                raise RuntimeError(f"Falha em {len(errors)} relatório(s): {failed_days}".rstrip(": "))
            cleanup_orphans()
        except Exception:
            try:
                final.ORIGINAL_RESTORE(before, mode="replace")
                cleanup_orphans()
            except Exception:
                pass
            raise

    return {
        "ok": True,
        "mode": mode,
        "snapshot": str(snapshot_path.relative_to(DATA_DIR)),
        "devices": device_result,
        "devicesSaved": int(device_result.get("count") or len(fixed["devices"])),
        "reportsImported": len(imported),
        "warnings": warnings,
        "errors": [],
    }


def apply_runtime_patches() -> None:
    final.apply_patches()
    core.upsert_equipment = runtime_upsert_equipment
    base.save_report = gated_save_report
    base.save_devices = gated_save_devices
    core.save_legacy_report = gated_save_report
    core.save_devices = gated_save_devices
    base.restore_payload = transactional_restore
    if hasattr(base, "restore_backup"):
        base.restore_backup = transactional_restore
    final.restore_payload = transactional_restore
    final.restore_backup = transactional_restore


def init_db() -> None:
    migrate_legacy_equipments()
    base.init_db()


save_devices = gated_save_devices
load_devices = base.load_devices
restore_payload = transactional_restore
restore_backup = transactional_restore
Handler = final.Handler


def main() -> None:
    apply_runtime_patches()
    init_db()
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8880"))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"YARA report system em http://{host}:{port}", flush=True)
    print(f"Dados persistentes: {DATA_DIR}", flush=True)
    server.serve_forever()
