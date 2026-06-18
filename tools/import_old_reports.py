from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server import init_db, import_report_object  # noqa: E402


def iter_json_files(path: Path):
    if path.is_file():
        yield path
        return
    yield from sorted(path.glob("*.json"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Importa relatórios JSON antigos para o banco SQLite novo.")
    parser.add_argument("source", help="Arquivo JSON ou pasta com JSONs antigos")
    parser.add_argument("--old-data-dir", default=None, help="Pasta data antiga para reaproveitar imagens físicas")
    args = parser.parse_args()

    source = Path(args.source).resolve()
    old_data_dir = Path(args.old_data_dir).resolve() if args.old_data_dir else None
    init_db()

    total = {"files": 0, "items": 0, "images": 0, "reused_images": 0, "errors": 0}
    for file_path in iter_json_files(source):
        try:
            report = json.loads(file_path.read_text(encoding="utf-8"))
            result = import_report_object(report, old_data_dir)
            total["files"] += 1
            total["items"] += result["items"]
            total["images"] += result["images"]
            total["reused_images"] += result["reused_images"]
            print(f"OK {file_path.name}: {result['items']} itens, {result['images']} imagens novas, {result['reused_images']} reaproveitadas")
        except Exception as exc:
            total["errors"] += 1
            print(f"ERRO {file_path.name}: {exc}")

    print("Resumo:", total)
    return 1 if total["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
