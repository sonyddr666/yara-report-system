#!/bin/sh
set -eu

DATA_DIR="${YARA_DATA_DIR:-/app/data}"
mkdir -p "$DATA_DIR/images" "$DATA_DIR/snapshots"

probe="$DATA_DIR/.write-test-$$"
printf 'ok' > "$probe"
rm -f "$probe"

echo "[entrypoint] dados persistentes em: $DATA_DIR"
echo "[entrypoint] iniciando servidor..."
exec python server.py
