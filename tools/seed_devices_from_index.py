from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server import init_db, save_devices, load_devices  # noqa: E402


def main() -> int:
    index_path = Path(__file__).resolve().parents[1] / "index.html"
    html = index_path.read_text(encoding="utf-8")
    match = re.search(r"const\s+DEFAULT_DEVICE_DATA\s*=\s*(\[.*?\]);", html, re.DOTALL)
    if not match:
        print("DEFAULT_DEVICE_DATA nao encontrado")
        return 1

    devices = json.loads(match.group(1))
    init_db()
    result = save_devices(devices)
    print(f"Base original salva: {result['count']} equipamentos")
    print(f"Base carregada do banco: {len(load_devices())} equipamentos")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
