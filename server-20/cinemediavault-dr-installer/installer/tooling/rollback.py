"""Rollback and uninstall.

Both operations share one non-negotiable rule: **media is never removed unless
the operator explicitly and separately asks for it**, and even then only the
directories CineMediaVault generated, never a source library.

Rollback replays the journal's backup entries in reverse order, restoring every
file the run replaced and removing every file it created. Uninstall goes
further and removes the installation itself, after taking a final backup of the
database and configuration so the decision is reversible.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from ..core.errors import RollbackError
from ..core.fsops import restore_backup_set
from ..core.journal import Journal

#: Units the installer may create. Uninstall stops and removes exactly these.
MANAGED_UNITS = (
    "cinemediavault.service",
    "cinemediavault-health.service",
    "cinemediavault-health.timer",
    "cinemediavault-refresh.service",
    "cinemediavault-refresh.timer",
    "cinemediavault-backup.service",
    "cinemediavault-backup.timer",
    "cinemediavault-metadata.service",
    "cinemediavault-metadata.timer",
    "cinemediavault-thumbnails.service",
    "cinemediavault-thumbnails.timer",
    "cinemediavault-bookvault.service",
    "cinemediavault-subtitles.service",
    "cinemediavault-setup.service",
    "cinemediavault-module@.service",
)


def rollback(ctx, *, to_step: str = "") -> dict:
    """Undo the recorded changes of the most recent run."""
    journal = ctx.journal
    if not journal.order:
        raise RollbackError("no installation run is recorded; nothing to roll back")

    ctx.logger.info(f"rolling back run {journal.run_id}")

    entries: list[dict] = []
    for step_id in reversed(journal.order):
        if to_step and step_id == to_step:
            ctx.logger.info(f"stopping rollback at {to_step}")
            break
        record = journal.steps.get(step_id)
        if record and record.backups:
            entries.extend(record.backups)

    if ctx.dry_run:
        ctx.logger.info(f"[dry-run] would restore {len(entries)} path(s)")
        return {"restored": 0, "planned": len(entries), "dry_run": True}

    # Stop services first: restoring a unit file under a running service leaves
    # the running process and the on-disk definition disagreeing.
    for unit in MANAGED_UNITS:
        if ctx.runner.systemd_unit_active(unit):
            ctx.runner.systemctl("stop", unit, check=False)

    restored = restore_backup_set(entries, logger=ctx.logger)
    ctx.runner.systemctl("daemon-reload", check=False)

    for step_id in reversed(journal.order):
        if to_step and step_id == to_step:
            break
        record = journal.steps.get(step_id)
        if record:
            record.status = "pending"
            record.backups = []
    journal.phase = "rolled-back"
    journal.save()

    ctx.logger.success(f"restored {restored} path(s)")
    return {"restored": restored, "planned": len(entries), "dry_run": False}


def uninstall(ctx, *, remove_media: bool = False, remove_state: bool = False,
              keep_backups: bool = True) -> dict:
    """Remove the installation.

    ``remove_state`` deletes the database, metadata and generated artwork.
    ``remove_media`` is separate, far more destructive, and only ever removes
    directories CineMediaVault itself generated - never a source library.
    """
    layout = ctx.layout
    removed: list[str] = []
    kept: list[str] = []

    ctx.logger.info("uninstalling CineMediaVault")

    # -- final safety backup -----------------------------------------------
    if not ctx.dry_run and layout.db_file.exists():
        stamp = time.strftime("%Y%m%d-%H%M%S")
        final = Path("/var/backups") / f"cinemediavault-final-{stamp}"
        try:
            final.mkdir(parents=True, exist_ok=True)
            shutil.copy2(layout.db_file, final / layout.db_file.name)
            if layout.config_file.exists():
                shutil.copy2(layout.config_file, final / layout.config_file.name)
            ctx.logger.success(f"final backup written to {final}")
            kept.append(str(final))
        except OSError as exc:
            ctx.logger.warning(f"could not write the final backup: {exc}")

    # -- stop and remove units ------------------------------------------------
    for unit in MANAGED_UNITS:
        if ctx.dry_run:
            ctx.logger.info(f"[dry-run] would stop and remove {unit}")
            continue
        ctx.runner.systemctl("disable", "--now", unit, check=False)
        path = Path("/etc/systemd/system") / unit
        if path.exists():
            path.unlink()
            removed.append(str(path))
    if not ctx.dry_run:
        ctx.runner.systemctl("daemon-reload", check=False)
        ctx.runner.systemctl("reset-failed", check=False)

    # -- containers -------------------------------------------------------------
    compose = layout.compose_dir / "epg" / "docker-compose.yml"
    if compose.is_file() and ctx.runner.has("docker"):
        if ctx.dry_run:
            ctx.logger.info("[dry-run] would remove the EPG collector container")
        else:
            ctx.runner.run(["docker", "compose", "-f", str(compose), "down",
                            "--volumes", "--remove-orphans"],
                           check=False, timeout=180)

    # -- fstab -------------------------------------------------------------------
    from ..discovery.mounts import remove_managed_entries
    remove_managed_entries(dry_run=ctx.dry_run, logger=ctx.logger)

    # -- firewall -----------------------------------------------------------------
    if ctx.runner.has("ufw") and not ctx.dry_run:
        status = ctx.runner.probe(["ufw", "status", "numbered"])
        # Delete highest-numbered rules first so the numbering stays valid.
        targets = [
            line.split("]")[0].strip("[ ")
            for line in reversed(status.stdout.splitlines())
            if "CineMediaVault" in line and line.strip().startswith("[")
        ]
        for number in targets:
            ctx.runner.run(["ufw", "--force", "delete", number],
                           check=False, timeout=30)

    # -- files ---------------------------------------------------------------------
    to_remove = [layout.install_root, Path("/etc/logrotate.d/cinemediavault"),
                 Path("/usr/local/sbin/cinevaultctl"),
                 Path("/usr/local/sbin/cinevault-create")]
    if remove_state:
        to_remove.extend([layout.config_root, layout.cache_root, layout.log_root])
        if keep_backups:
            kept.append(str(layout.backup_dir))
            to_remove.extend(
                p for p in layout.state_root.iterdir()
                if layout.state_root.exists() and p != layout.backup_dir)
        else:
            to_remove.append(layout.state_root)
    else:
        kept.extend([str(layout.config_root), str(layout.state_root),
                     str(layout.log_root)])

    for path in to_remove:
        if ctx.dry_run:
            ctx.logger.info(f"[dry-run] would remove {path}")
            continue
        try:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            elif path.exists() or path.is_symlink():
                path.unlink()
            else:
                continue
            removed.append(str(path))
        except OSError as exc:
            ctx.logger.warning(f"could not remove {path}: {exc}")

    # -- media -----------------------------------------------------------------------
    media_removed: list[str] = []
    if remove_media:
        # Only generated output. Source libraries are never touched, whatever
        # the operator asked for, because a generated library can be rebuilt and
        # a source library cannot.
        generated = [ctx.get("media.comic_library_root")]
        for raw in generated:
            path = Path(raw) if raw else None
            if not path or not path.is_dir():
                continue
            if ctx.dry_run:
                ctx.logger.info(f"[dry-run] would remove generated library {path}")
                continue
            try:
                shutil.rmtree(path)
                media_removed.append(str(path))
                ctx.logger.info(f"removed generated library {path}")
            except OSError as exc:
                ctx.logger.warning(f"could not remove {path}: {exc}")
        ctx.logger.info(
            "source media libraries (movies, TV, music, books, comics, games, "
            "recordings) were NOT removed. Delete them yourself if that is "
            "genuinely what you want.")

    # -- service account ---------------------------------------------------------------
    if remove_state and not ctx.dry_run:
        ctx.runner.run(["userdel", ctx.service_user], check=False, timeout=30)
        ctx.runner.run(["groupdel", ctx.service_group], check=False, timeout=30)

    ctx.logger.success(f"removed {len(removed)} path(s)")
    return {
        "removed": removed,
        "kept": kept,
        "media_removed": media_removed,
        "state_removed": remove_state,
        "dry_run": ctx.dry_run,
    }
