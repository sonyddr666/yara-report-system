#!/bin/bash
set -e

mkdir -p /app/data/images

if [ ! -f /app/database.db ] || [ ! -s /app/database.db ]; then
    echo "[entrypoint] database vazio ou ausente, copiando do stock..."
    cp /app/db-stock/database.db /app/database.db
fi

if [ -z "$(ls -A /app/data/images/ 2>/dev/null)" ]; then
    echo "[entrypoint] imagens vazias, copiando do stock..."
    cp -r /app/data-stock/* /app/data/
fi

echo "[entrypoint] iniciando servidor..."
exec python server.py