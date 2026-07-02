from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import init_db, load_devices, restore_backup


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source")
    parser.add_argument("--mode", choices=("merge", "replace"), default="merge")
    args = parser.parse_args()
    init_db()
    path = Path(args.source).resolve()
    if path.is_file():
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        backup = value if isinstance(value, dict) and isinstance(value.get("reports"), list) else {
            "version": 5, "devices": load_devices(), "reports": [value]
        }
    else:
        reports = [json.loads(item.read_text(encoding="utf-8-sig")) for item in sorted(path.glob("*.json"))]
        backup = {"version": 5, "devices": load_devices(), "reports": reports}
    result = restore_backup(backup, mode=args.mode)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
