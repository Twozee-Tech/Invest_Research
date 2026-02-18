#!/bin/sh
# Ensure runtime directories exist (fallback if volumes not mounted)
mkdir -p /app/data /app/logs

# Seed runtime config from baked-in default if not yet present
if [ ! -f /app/data/config.yaml ]; then
    cp /app/default_config.yaml /app/data/config.yaml
    echo "[entrypoint] Seeded /app/data/config.yaml from default"
fi

exec "$@"
