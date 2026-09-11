"""Upgrade and configuration migration.

An upgrade is deliberately the same code path as an install: migrate the
configuration forward, back everything up, then re-run the idempotent steps.
Steps whose fingerprint is unchanged skip themselves, so an upgrade touches only
what actually differs.
"""

from __future__ import annotations

import time
from typing import Callable

from ..config import loader
from ..config.schema import apply_defaults, errors, get, put, validate
from ..core.errors import ConfigError, InstallerError
from ..version import CONFIG_SCHEMA_VERSION, INSTALLER_VERSION

#: schema version -> migration. Each takes a config and returns the next
#: version's config. Migrations must be pure and must not lose data: an unknown
#: key is carried forward untouched rather than dropped.
Migration = Callable[[dict], dict]


def _migrate_0_to_1(config: dict) -> dict:
    """Pre-schema configurations had no meta block and flat media keys."""
    out = dict(config)
    put(out, "meta.schema_version", 1)
    # The very first portable installer used MOVIE_ROOT-style flat keys.
    legacy = {
        "MOVIE_ROOT": "media.movies_root",
        "TV_ROOT": "media.tv_root",
        "MUSIC_ROOT": "media.music_root",
        "COMICS_ROOT": "media.comics_root",
        "COMIC_LIBRARY_ROOT": "media.comic_library_root",
    }
    for old, new in legacy.items():
        if old in out and not get(out, new):
            put(out, new, out.pop(old))
    return out


MIGRATIONS: dict[int, Migration] = {
    0: _migrate_0_to_1,
}


def current_schema_version(config: dict) -> int:
    try:
        return int(get(config, "meta.schema_version", 0) or 0)
    except (TypeError, ValueError):
        return 0


def migrate(config: dict, *, logger=None) -> tuple[dict, list[str]]:
    """Bring a configuration up to the current schema version."""
    from ..core.logging import get_logger
    logger = logger or get_logger()

    version = current_schema_version(config)
    applied: list[str] = []
    working = dict(config)

    if version > CONFIG_SCHEMA_VERSION:
        raise ConfigError(
            f"this configuration is schema v{version}, but this installer "
            f"understands v{CONFIG_SCHEMA_VERSION}. Upgrade the installer, or "
            f"restore the configuration that matches it.")

    while version < CONFIG_SCHEMA_VERSION:
        migration = MIGRATIONS.get(version)
        if migration is None:
            raise ConfigError(
                f"no migration from schema v{version} to v{version + 1}")
        logger.info(f"migrating configuration v{version} -> v{version + 1}")
        working = migration(working)
        applied.append(f"v{version}->v{version + 1}")
        version = current_schema_version(working)
        if version <= 0:
            raise ConfigError("migration did not advance the schema version")

    return working, applied


def plan_upgrade(ctx) -> dict:
    """Describe what an upgrade would change, without changing anything."""
    from ..engine import Engine

    installed = _installed_version(ctx)
    return {
        "installed_version": installed,
        "package_version": INSTALLER_VERSION,
        "config_schema": current_schema_version(ctx.config),
        "target_schema": CONFIG_SCHEMA_VERSION,
        "same_version": installed == INSTALLER_VERSION,
        "steps": Engine(ctx).plan(),
    }


def upgrade(ctx, *, force: bool = False) -> dict:
    """Run an upgrade: migrate, back up, then re-converge."""
    from ..engine import Engine
    from . import backup as backup_tool

    installed = _installed_version(ctx)
    if installed == INSTALLER_VERSION and not force and not ctx.force_steps:
        ctx.logger.info(
            f"already at version {INSTALLER_VERSION}. Re-running anyway repairs "
            f"drift; pass --force to do that.")

    migrated, applied = migrate(ctx.config, logger=ctx.logger)
    if applied:
        ctx.config = apply_defaults(migrated)

    problems = errors(validate(ctx.config))
    if problems:
        raise ConfigError(
            "the migrated configuration is not valid:\n  "
            + "\n  ".join(str(p) for p in problems))

    if not ctx.dry_run:
        ctx.logger.info("taking a pre-upgrade backup")
        backup_tool.create(ctx, label="pre-upgrade")

    report = Engine(ctx).run()

    return {
        "from_version": installed,
        "to_version": INSTALLER_VERSION,
        "migrations": applied,
        "report": report.to_json(),
        "ok": report.ok,
    }


def _installed_version(ctx) -> str:
    import json
    try:
        data = json.loads(ctx.layout.version_file.read_text(encoding="utf-8"))
        return str(data.get("installer_version") or "none")
    except (OSError, ValueError):
        return "none"
