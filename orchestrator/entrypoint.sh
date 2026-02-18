#!/bin/sh
# Seed runtime config from baked-in default if not yet present
mkdir -p /app/data
if [ ! -f /app/data/config.yaml ]; then
    cp /app/default_config.yaml /app/data/config.yaml
    echo "[entrypoint] Seeded /app/data/config.yaml from default"
fi
exec "$@"
