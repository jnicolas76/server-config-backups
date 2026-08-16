#!/usr/bin/env python3
"""Small Java-style .properties loader for the portable transcode suite."""

import os
from pathlib import Path


def load_properties(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def default_config_path(app_name: str) -> Path:
    env_key = f"{app_name.upper().replace('-', '_')}_CONFIG"
    if os.environ.get(env_key):
        return Path(os.environ[env_key])
    return Path(__file__).resolve().parents[1] / "config" / "application.properties"


def get_path(config: dict[str, str], key: str, default: Path | str) -> Path:
    return Path(config.get(key, str(default))).expanduser()


def get_float(config: dict[str, str], key: str, default: float) -> float:
    try:
        return float(config.get(key, str(default)))
    except ValueError:
        return default


def get_int(config: dict[str, str], key: str, default: int) -> int:
    try:
        return int(config.get(key, str(default)))
    except ValueError:
        return default


def get_bool(config: dict[str, str], key: str, default: bool = False) -> bool:
    value = config.get(key)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "y", "on"}
