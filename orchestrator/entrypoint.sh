#!/bin/sh
# Ensure runtime directories are writable
mkdir -p /app/data /app/logs
chmod 777 /app/data /app/logs

# Seed runtime config from baked-in default if not yet present
if [ ! -f /app/data/config.yaml ]; then
    cp /app/default_config.yaml /app/data/config.yaml
    echo "[entrypoint] Seeded /app/data/config.yaml from default"
fi

exec "$@"
