"""Shared config load/save used by all dashboard pages."""

from pathlib import Path

import yaml

CONFIG_PATH = Path("data/config.yaml")
_BAKED_DEFAULT = Path("/app/default_config.yaml")


def load_config(fallback: dict | None = None) -> dict:
    """Load runtime config, falling back to the baked-in default if missing."""
    for path in [CONFIG_PATH, _BAKED_DEFAULT]:
        try:
            with open(path) as f:
                data = yaml.safe_load(f)
                return data if data else {}
        except (OSError, FileNotFoundError):
            continue
    return fallback if fallback is not None else {}


def save_config(config: dict) -> None:
    """Write config, creating data/ directory if needed."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
