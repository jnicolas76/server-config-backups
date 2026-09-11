"""Backup and restore of everything that is not media.

What a backup contains: the configuration (with secrets, because a restore that
cannot reach TMDb is not a restore), the SQLite databases taken through the
SQLite backup API so they are consistent, the generated metadata and artwork,
and the module and modules-page state.

What it never contains: media files, HLS or subtitle caches, or the TLS private
key. Media is far too large and is not ours to copy; caches regenerate; the
private key belongs with the certificate authority that issued it.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tarfile
import tempfile
import time
from pathlib import Path

from ..core.errors import InstallerError
from ..version import CONFIG_SCHEMA_VERSION, INSTALLER_VERSION

#: Included, relative to the state root.
STATE_INCLUDES = ("metadata", "music-art", "module-logos", "modules.json",
                  "playback-mode.txt", "poster-rotation-cache.json",
                  "epg-extend-map.json", "hdhr-guide-cache.json",
                  "subtitles/settings.json", "transcode/queue-settings.json",
                  "bookvault")

#: Never included, whatever else matches.
NEVER_INCLUDE = ("hls", "subtitles/venv", "mobile-downloads", "change-backups",
                 "backups", "whisper")


def create(ctx, *, destination: str | None = None, include_secrets: bool = True,
           label: str = "manual") -> Path:
    """Write a backup archive and return its path."""
    layout = ctx.layout
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out_dir = Path(destination) if destination else layout.backup_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    archive = out_dir / f"cinemediavault-backup-{stamp}-{label}.tar.gz"

    if ctx.dry_run:
        ctx.logger.info(f"[dry-run] would write a backup to {archive}")
        return archive

    with tempfile.TemporaryDirectory(prefix="cmv-backup-") as tmp:
        staging = Path(tmp) / "cinemediavault-backup"
        staging.mkdir(parents=True)

        manifest = {
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "installer_version": INSTALLER_VERSION,
            "config_schema_version": CONFIG_SCHEMA_VERSION,
            "instance_name": ctx.get("meta.instance_name"),
            "includes_secrets": include_secrets,
            "contents": [],
            "excluded": ["media files", "HLS cache", "subtitle cache",
                         "TLS private key", "Whisper models"],
        }

        # -- databases, consistently -------------------------------------
        db_out = staging / "db"
        db_out.mkdir()
        for source in (layout.db_file, layout.music_db_file):
            if not source.exists():
                continue
            target = db_out / source.name
            _sqlite_backup(source, target)
            manifest["contents"].append(f"db/{source.name}")
            ctx.logger.info(f"backed up {source.name}")

        # -- configuration ---------------------------------------------------
        config_out = staging / "config"
        config_out.mkdir()
        for source in (layout.config_file, layout.env_file):
            if source.exists():
                shutil.copy2(source, config_out / source.name)
                manifest["contents"].append(f"config/{source.name}")
        modules_dir = layout.config_root / "modules"
        if modules_dir.is_dir():
            shutil.copytree(modules_dir, config_out / "modules")
            manifest["contents"].append("config/modules/")
        if include_secrets and layout.secrets_file.exists():
            shutil.copy2(layout.secrets_file, config_out / layout.secrets_file.name)
            os.chmod(config_out / layout.secrets_file.name, 0o600)
            manifest["contents"].append("config/secrets.env")

        # The TLS private key is deliberately not copied. A backup travels; a
        # private key should not.
        if (layout.tls_dir / "cinemediavault.crt").exists():
            shutil.copy2(layout.tls_dir / "cinemediavault.crt",
                         config_out / "cinemediavault.crt")
            manifest["contents"].append("config/cinemediavault.crt (certificate only)")

        # -- state ---------------------------------------------------------------
        state_out = staging / "state"
        state_out.mkdir()
        for relative in STATE_INCLUDES:
            source = layout.state_root / relative
            if not source.exists():
                continue
            target = state_out / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                shutil.copytree(source, target,
                                ignore=shutil.ignore_patterns(*NEVER_INCLUDE))
            else:
                shutil.copy2(source, target)
            manifest["contents"].append(f"state/{relative}")

        (staging / "MANIFEST.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

        with tarfile.open(archive, "w:gz") as tar:
            tar.add(staging, arcname="cinemediavault-backup")

    os.chmod(archive, 0o600 if include_secrets else 0o640)
    size = archive.stat().st_size
    ctx.logger.success(f"backup written: {archive} ({size / 1024 / 1024:.1f} MB)")
    _prune(ctx, out_dir)
    return archive


def _sqlite_backup(source: Path, target: Path) -> None:
    """Copy a live database consistently and verify the copy."""
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=60)
    dst = sqlite3.connect(str(target))
    try:
        src.backup(dst)
        check = dst.execute("PRAGMA integrity_check").fetchone()[0]
        if str(check).lower() != "ok":
            raise InstallerError(
                f"backup of {source.name} failed its integrity check: {check}")
    finally:
        dst.close()
        src.close()


def _prune(ctx, directory: Path) -> None:
    keep = int(ctx.get("ops.db_backup_keep") or 10)
    archives = sorted(directory.glob("cinemediavault-backup-*.tar.gz"),
                      key=lambda p: p.stat().st_mtime, reverse=True)
    for old in archives[max(1, keep):]:
        try:
            old.unlink()
            ctx.logger.debug(f"pruned old backup {old.name}")
        except OSError:
            pass


def inspect(archive: str | Path) -> dict:
    """Read a backup's manifest without extracting anything else."""
    with tarfile.open(archive, "r:gz") as tar:
        member = tar.extractfile("cinemediavault-backup/MANIFEST.json")
        if member is None:
            raise InstallerError(f"{archive} has no manifest; it is not a "
                                 f"CineMediaVault backup")
        return json.loads(member.read().decode("utf-8"))


def restore(ctx, archive: str | Path, *, restore_secrets: bool = True,
            restore_config: bool = True) -> dict:
    """Restore a backup over the current installation."""
    layout = ctx.layout
    path = Path(archive)
    if not path.is_file():
        raise InstallerError(f"backup not found: {path}")

    manifest = inspect(path)
    ctx.logger.info(f"restoring backup from {manifest.get('created_at', 'unknown date')}")

    if manifest.get("config_schema_version", 0) > CONFIG_SCHEMA_VERSION:
        raise InstallerError(
            f"this backup was written by a newer version (config schema "
            f"v{manifest['config_schema_version']}, this installer understands "
            f"v{CONFIG_SCHEMA_VERSION}). Upgrade the installer first.")

    if ctx.dry_run:
        ctx.logger.info(f"[dry-run] would restore: {', '.join(manifest['contents'])}")
        return {"restored": [], "dry_run": True, "manifest": manifest}

    # A restore replaces the database; take a safety copy of what is there now.
    if layout.db_file.exists():
        create(ctx, label="pre-restore", include_secrets=False)

    ctx.runner.systemctl("stop", "cinemediavault.service", check=False)

    restored: list[str] = []
    with tempfile.TemporaryDirectory(prefix="cmv-restore-") as tmp:
        with tarfile.open(path, "r:gz") as tar:
            _safe_extract(tar, Path(tmp))
        root = Path(tmp) / "cinemediavault-backup"

        for source in (root / "db").glob("*.db"):
            target = layout.db_dir / source.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            restored.append(str(target))

        if restore_config:
            for name in ("cinemediavault.yaml", "cinevault.env"):
                source = root / "config" / name
                if source.exists():
                    shutil.copy2(source, layout.config_root / name)
                    os.chmod(layout.config_root / name, 0o640)
                    restored.append(str(layout.config_root / name))
            modules = root / "config" / "modules"
            if modules.is_dir():
                target = layout.config_root / "modules"
                shutil.rmtree(target, ignore_errors=True)
                shutil.copytree(modules, target)
                restored.append(str(target))

        if restore_secrets and (root / "config" / "secrets.env").exists():
            shutil.copy2(root / "config" / "secrets.env", layout.secrets_file)
            os.chmod(layout.secrets_file, 0o640)
            restored.append(str(layout.secrets_file))

        state = root / "state"
        if state.is_dir():
            for item in state.iterdir():
                target = layout.state_root / item.name
                if item.is_dir():
                    shutil.rmtree(target, ignore_errors=True)
                    shutil.copytree(item, target)
                else:
                    shutil.copy2(item, target)
                restored.append(str(target))

    ctx.runner.run(["chown", "-R", f"{ctx.service_user}:{ctx.service_group}",
                    str(layout.state_root)], check=False)
    ctx.runner.systemctl("start", "cinemediavault.service", check=False)

    ctx.logger.success(f"restored {len(restored)} path(s)")
    return {"restored": restored, "dry_run": False, "manifest": manifest}


def _safe_extract(tar: tarfile.TarFile, destination: Path) -> None:
    """Extract, refusing any member that would escape the destination.

    A backup archive is trusted input in normal use, but a restore runs as root
    and an archive can arrive from anywhere.
    """
    root = destination.resolve()
    for member in tar.getmembers():
        target = (root / member.name).resolve()
        if not str(target).startswith(str(root) + os.sep) and target != root:
            raise InstallerError(
                f"refusing to extract {member.name}: it escapes the destination")
        if member.issym() or member.islnk():
            link_target = (target.parent / member.linkname).resolve()
            if not str(link_target).startswith(str(root) + os.sep):
                raise InstallerError(
                    f"refusing to extract link {member.name}: it points outside "
                    f"the destination")
    tar.extractall(destination)     # noqa: S202 - every member validated above
