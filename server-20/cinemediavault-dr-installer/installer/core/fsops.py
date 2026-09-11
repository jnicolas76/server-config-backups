"""Filesystem operations: atomic writes, pre-change backups, safe ownership.

Two invariants hold everywhere in the installer:

* **No partial file is ever visible.** Every write goes to a temporary file in
  the same directory, is fsynced, then renamed over the target. A power loss
  leaves either the old file or the new one, never a half-written one.
* **Nothing is overwritten without a backup.** :func:`write_file` snapshots any
  existing target into the run's backup directory first, so ``cinevaultctl
  rollback`` can put every touched file back exactly as it was.
"""

from __future__ import annotations

import grp
import hashlib
import os
import pwd
import shutil
import stat
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from .errors import InstallerError
from .logging import InstallLogger, get_logger


@dataclass
class BackupEntry:
    target: str
    backup: str | None      # None means "did not exist before"
    mode: int | None = None
    uid: int | None = None
    gid: int | None = None


@dataclass
class BackupSet:
    """Records every file the installer replaced during one run."""

    directory: Path
    entries: list[BackupEntry] = field(default_factory=list)

    def add(self, entry: BackupEntry) -> None:
        self.entries.append(entry)

    def to_json(self) -> list[dict]:
        return [
            {"target": e.target, "backup": e.backup, "mode": e.mode,
             "uid": e.uid, "gid": e.gid}
            for e in self.entries
        ]


def sha256_file(path: str | os.PathLike) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def can_change_owner() -> bool:
    """Only root can chown to another account.

    The installer requires root in production - preflight refuses otherwise -
    so this is really about the unprivileged paths: dry runs, the plan command,
    and the test suite, all of which must be able to exercise the same code
    without pretending to be root.
    """
    return os.geteuid() == 0


def resolve_owner(user: str | int | None, group: str | int | None,
                  *, missing_ok: bool = False) -> tuple[int, int]:
    """Resolve a user/group to numeric ids. ``-1`` means "leave unchanged".

    ``missing_ok`` is what makes a dry run possible: the service account is
    normally created by an earlier step, so during a dry run it does not exist
    yet and looking it up would fail. A dry run never chowns anything, so an
    unresolvable name is simply reported as "unchanged".
    """
    uid = -1
    gid = -1
    if user is not None:
        try:
            uid = (int(user) if isinstance(user, int) or str(user).isdigit()
                   else pwd.getpwnam(str(user)).pw_uid)
        except KeyError:
            if not missing_ok:
                raise
    if group is not None:
        try:
            gid = (int(group) if isinstance(group, int) or str(group).isdigit()
                   else grp.getgrnam(str(group)).gr_gid)
        except KeyError:
            if not missing_ok:
                raise
    return uid, gid


def backup_path_for(backup_dir: Path, target: Path) -> Path:
    """Mirror the absolute target path inside the backup directory."""
    rel = str(target).lstrip("/")
    return backup_dir / "files" / rel


def snapshot(target: str | os.PathLike, backups: BackupSet | None,
             logger: InstallLogger | None = None) -> BackupEntry | None:
    """Copy *target* into the backup set before it is modified or removed."""
    logger = logger or get_logger()
    path = Path(target)
    if backups is None:
        return None
    if not path.exists() and not path.is_symlink():
        entry = BackupEntry(target=str(path), backup=None)
        backups.add(entry)
        return entry
    dest = backup_path_for(backups.directory, path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    st = path.lstat()
    if path.is_dir() and not path.is_symlink():
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(path, dest, symlinks=True)
    else:
        shutil.copy2(path, dest, follow_symlinks=False)
    entry = BackupEntry(target=str(path), backup=str(dest),
                        mode=stat.S_IMODE(st.st_mode), uid=st.st_uid, gid=st.st_gid)
    backups.add(entry)
    logger.debug(f"backed up {path} -> {dest}")
    return entry


def write_file(
    target: str | os.PathLike,
    content: str | bytes,
    *,
    mode: int = 0o644,
    user: str | int | None = None,
    group: str | int | None = None,
    backups: BackupSet | None = None,
    dry_run: bool = False,
    logger: InstallLogger | None = None,
) -> bool:
    """Atomically write *content* to *target*.

    Returns ``True`` when the file changed, ``False`` when it was already
    byte-identical with the right mode and owner - which is what makes a second
    installer run a no-op rather than a rewrite.
    """
    logger = logger or get_logger()
    path = Path(target)
    data = content.encode("utf-8") if isinstance(content, str) else content
    uid, gid = resolve_owner(user, group, missing_ok=dry_run)

    if path.exists() and path.is_file():
        try:
            current = path.read_bytes()
            st = path.stat()
            same_mode = stat.S_IMODE(st.st_mode) == mode
            if can_change_owner():
                same_owner = ((uid == -1 or st.st_uid == uid)
                              and (gid == -1 or st.st_gid == gid))
            else:
                # Ownership we cannot change must not count as a difference,
                # otherwise an unprivileged run would report an endless
                # "changed" for a file that is already correct.
                same_owner = True
            if current == data and same_mode and same_owner:
                logger.debug(f"unchanged: {path}")
                return False
        except OSError:
            pass

    if dry_run:
        logger.info(f"[dry-run] would write {path} ({len(data)} bytes, mode {mode:04o})")
        return True

    snapshot(path, backups, logger)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        if (uid != -1 or gid != -1) and can_change_owner():
            os.chown(tmp, uid, gid)
        os.replace(tmp, path)
        # Durability of the rename itself.
        dir_fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    logger.debug(f"wrote {path} (mode {mode:04o})")
    return True


def ensure_dir(
    target: str | os.PathLike,
    *,
    mode: int = 0o755,
    user: str | int | None = None,
    group: str | int | None = None,
    dry_run: bool = False,
    logger: InstallLogger | None = None,
) -> bool:
    """Create a directory if needed and correct its mode/owner. Idempotent."""
    logger = logger or get_logger()
    path = Path(target)
    uid, gid = resolve_owner(user, group, missing_ok=dry_run)
    changed = False

    if path.exists() and not path.is_dir():
        raise InstallerError(f"{path} exists and is not a directory")

    if not path.exists():
        if dry_run:
            logger.info(f"[dry-run] would create directory {path} (mode {mode:04o})")
            return True
        path.mkdir(parents=True, exist_ok=True)
        changed = True

    if dry_run:
        return changed

    st = path.stat()
    if stat.S_IMODE(st.st_mode) != mode:
        os.chmod(path, mode)
        changed = True
    needs_chown = (uid != -1 and st.st_uid != uid) or (gid != -1 and st.st_gid != gid)
    if needs_chown and can_change_owner():
        os.chown(path, uid, gid)
        changed = True
    if changed:
        logger.debug(f"directory {path} (mode {mode:04o})")
    return changed


def copy_tree(
    source: str | os.PathLike,
    target: str | os.PathLike,
    *,
    mode_files: int = 0o644,
    mode_dirs: int = 0o755,
    mode_exec: int = 0o755,
    user: str | int | None = None,
    group: str | int | None = None,
    backups: BackupSet | None = None,
    dry_run: bool = False,
    logger: InstallLogger | None = None,
    exclude: tuple[str, ...] = ("__pycache__", ".git", "*.pyc", ".DS_Store"),
) -> int:
    """Copy a payload tree, writing each file atomically. Returns files changed."""
    import fnmatch

    logger = logger or get_logger()
    src = Path(source)
    dst = Path(target)
    if not src.is_dir():
        raise InstallerError(f"payload directory missing: {src}")

    changed = 0
    ensure_dir(dst, mode=mode_dirs, user=user, group=group, dry_run=dry_run, logger=logger)
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if not any(fnmatch.fnmatch(d, p) for p in exclude)]
        rel = Path(root).relative_to(src)
        for d in dirs:
            ensure_dir(dst / rel / d, mode=mode_dirs, user=user, group=group,
                       dry_run=dry_run, logger=logger)
        for name in files:
            if any(fnmatch.fnmatch(name, p) for p in exclude):
                continue
            spath = Path(root) / name
            dpath = dst / rel / name
            is_exec = os.access(spath, os.X_OK) or name.endswith(".sh")
            file_mode = mode_exec if is_exec else mode_files
            if write_file(dpath, spath.read_bytes(), mode=file_mode, user=user,
                          group=group, backups=backups, dry_run=dry_run, logger=logger):
                changed += 1
    return changed


def remove_path(
    target: str | os.PathLike,
    *,
    backups: BackupSet | None = None,
    dry_run: bool = False,
    logger: InstallLogger | None = None,
) -> bool:
    """Remove a file or directory after backing it up. Idempotent."""
    logger = logger or get_logger()
    path = Path(target)
    if not path.exists() and not path.is_symlink():
        return False
    if dry_run:
        logger.info(f"[dry-run] would remove {path}")
        return True
    snapshot(path, backups, logger)
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()
    logger.debug(f"removed {path}")
    return True


def new_backup_set(root: str | os.PathLike, label: str = "install") -> BackupSet:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    directory = Path(root) / f"{stamp}-{label}"
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    return BackupSet(directory=directory)


def restore_backup_set(entries: list[dict], *, logger: InstallLogger | None = None) -> int:
    """Undo a recorded backup set. Returns the number of paths restored."""
    logger = logger or get_logger()
    restored = 0
    # Restore deepest paths first so parent directories still exist.
    for item in sorted(entries, key=lambda e: len(str(e.get("target", ""))), reverse=True):
        target = Path(item["target"])
        backup = item.get("backup")
        try:
            if backup is None:
                # The file did not exist before this run: remove what we added.
                if target.is_dir() and not target.is_symlink():
                    shutil.rmtree(target, ignore_errors=True)
                elif target.exists() or target.is_symlink():
                    target.unlink()
                restored += 1
                continue
            source = Path(backup)
            if not source.exists():
                logger.warning(f"backup missing, cannot restore {target}")
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target, ignore_errors=True)
            elif target.exists() or target.is_symlink():
                target.unlink()
            if source.is_dir():
                shutil.copytree(source, target, symlinks=True)
            else:
                shutil.copy2(source, target, follow_symlinks=False)
            if item.get("mode") is not None:
                os.chmod(target, item["mode"])
            if item.get("uid") is not None and item.get("gid") is not None:
                try:
                    os.chown(target, item["uid"], item["gid"])
                except (PermissionError, OSError):
                    pass
            restored += 1
        except OSError as exc:
            logger.warning(f"could not restore {target}: {exc}")
    return restored


def free_bytes(path: str | os.PathLike) -> int:
    st = os.statvfs(str(path))
    return st.f_bavail * st.f_frsize


def total_bytes(path: str | os.PathLike) -> int:
    st = os.statvfs(str(path))
    return st.f_blocks * st.f_frsize


def human_bytes(value: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if abs(value) < 1024.0:
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024.0
    return f"{value:.1f} EiB"
