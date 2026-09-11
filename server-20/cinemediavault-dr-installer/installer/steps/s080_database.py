"""Create and prepare the SQLite databases.

The application creates its own schema on first start, so this step's job is
narrower and safer: make sure the database files exist with the right
ownership, enable WAL so a reader never blocks the scanner, and - crucially -
take a backup before an *upgrade* ever touches an existing database.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from ..core.errors import StepError
from ..core.fsops import ensure_dir, human_bytes
from .base import Step, StepResult


class PrepareDatabases(Step):
    id = "database"
    title = "Databases"
    description = "Create the SQLite databases and back up any existing one first."
    depends_on = ("paths.state_root", "modules.music")
    requires = ("directories", "users")

    def preview(self, ctx) -> str:
        if ctx.layout.db_file.exists():
            size = human_bytes(ctx.layout.db_file.stat().st_size)
            return (f"Existing database found ({size}). It will be backed up and "
                    f"left in place; no schema change is made by the installer.")
        return f"Create a new database at {ctx.layout.db_file}."

    def run(self, ctx) -> StepResult:
        ensure_dir(ctx.layout.db_dir, mode=0o750, user=ctx.service_user,
                   group=ctx.service_group, dry_run=ctx.dry_run, logger=ctx.logger)

        targets = [ctx.layout.db_file]
        if ctx.get("modules.music"):
            targets.append(ctx.layout.music_db_file)

        backed_up: list[str] = []
        created: list[str] = []
        for path in targets:
            if path.exists():
                backup = self._backup(ctx, path)
                if backup:
                    backed_up.append(backup)
            else:
                created.append(str(path))
            if not ctx.dry_run:
                self._prepare(ctx, path)

        summary_parts = []
        if created:
            summary_parts.append(f"created {len(created)} database(s)")
        if backed_up:
            summary_parts.append(f"backed up {len(backed_up)} existing database(s)")
        if not summary_parts:
            summary_parts.append("databases already present")

        return StepResult(
            changed=bool(created),
            summary="; ".join(summary_parts),
            data={"created": created, "backups": backed_up},
        )

    # ------------------------------------------------------------------
    def _backup(self, ctx, path: Path) -> str | None:
        """Consistent online backup via the SQLite backup API."""
        if ctx.dry_run:
            ctx.logger.info(f"[dry-run] would back up {path}")
            return None
        ensure_dir(ctx.layout.backup_dir, mode=0o750, user=ctx.service_user,
                   group=ctx.service_group, logger=ctx.logger)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        destination = ctx.layout.backup_dir / f"{path.stem}-{stamp}-preinstall.db"
        try:
            source = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
            target = sqlite3.connect(str(destination))
            try:
                source.backup(target)
                check = target.execute("PRAGMA integrity_check").fetchone()[0]
                if str(check).lower() != "ok":
                    raise StepError(
                        f"pre-install backup of {path} failed its integrity check "
                        f"({check}). Refusing to continue against a damaged "
                        f"database.", step=self.id, recoverable=False)
            finally:
                target.close()
                source.close()
        except sqlite3.Error as exc:
            raise StepError(f"could not back up {path}: {exc}", step=self.id) from exc
        ctx.logger.success(f"backed up {path.name} -> {destination.name}")
        return str(destination)

    def _prepare(self, ctx, path: Path) -> None:
        """Create the file if needed and set the pragmas the application expects."""
        import grp
        import os
        import pwd

        existed = path.exists()
        connection = sqlite3.connect(str(path), timeout=30)
        try:
            # WAL lets the web process read while a scan writes. NORMAL sync is
            # the right trade-off for a media catalogue on a UPS-less home server:
            # a crash can lose the last transaction, never the database.
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.commit()
        finally:
            connection.close()

        if not existed:
            ctx.logger.info(f"created {path}")
        try:
            uid = pwd.getpwnam(ctx.service_user).pw_uid
            gid = grp.getgrnam(ctx.service_group).gr_gid
            for suffix in ("", "-wal", "-shm"):
                candidate = Path(str(path) + suffix)
                if candidate.exists():
                    os.chown(candidate, uid, gid)
                    os.chmod(candidate, 0o640)
        except KeyError as exc:
            raise StepError(f"service account missing: {exc}", step=self.id) from exc


def steps():
    return [PrepareDatabases()]
