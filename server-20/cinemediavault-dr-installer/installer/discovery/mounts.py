"""Mount inspection and safe fstab management.

The installer will add an fstab entry when the operator asks for it, but it
never removes or rewrites an entry it did not create. Managed lines are marked
with a comment tag so uninstall can find exactly its own additions.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from ..core.errors import InstallerError
from ..core.fsops import write_file

FSTAB = "/etc/fstab"
TAG = "# managed-by: cinemediavault"

#: Options every managed network mount gets. ``nofail`` and the automount pair
#: are what stop a missing NAS from blocking boot, and ``_netdev`` stops the
#: mount from being attempted before the network is up.
BASE_OPTIONS = {
    "nfs": ["_netdev", "nofail", "x-systemd.automount",
            "x-systemd.requires=network-online.target",
            "x-systemd.after=network-online.target"],
    "cifs": ["_netdev", "nofail", "iocharset=utf8", "x-systemd.automount",
             "x-systemd.requires=network-online.target",
             "x-systemd.after=network-online.target"],
}

#: Options that would let a mount hang the boot or silently hide a failure.
FORBIDDEN_OPTIONS = {"hard,intr", "bg"}

OPTION_RE = re.compile(r"^[A-Za-z0-9_,=./:@\-+]*$")


def list_mounts(runner) -> list[dict]:
    """Every current mount, from findmnt."""
    result = runner.probe(
        ["findmnt", "-rn", "-o", "TARGET,SOURCE,FSTYPE,OPTIONS"])
    mounts: list[dict] = []
    for line in result.stdout.splitlines():
        parts = line.split(" ", 3)
        if len(parts) < 3:
            continue
        mounts.append({
            "target": _unescape(parts[0]),
            "source": _unescape(parts[1]),
            "fstype": parts[2],
            "options": parts[3] if len(parts) > 3 else "",
        })
    return mounts


def _unescape(text: str) -> str:
    # findmnt escapes spaces as \x20
    return re.sub(r"\\x([0-9a-fA-F]{2})", lambda m: chr(int(m.group(1), 16)), text)


def is_mounted(path: str, runner) -> bool:
    target = os.path.normpath(path).rstrip("/") or "/"
    return any(m["target"].rstrip("/") == target for m in list_mounts(runner))


def build_options(kind: str, extra: str = "", credentials_file: str = "") -> str:
    """Merge operator options onto the safe defaults."""
    if kind not in BASE_OPTIONS:
        raise InstallerError(f"unsupported mount type: {kind}")
    if extra and not OPTION_RE.match(extra):
        raise InstallerError(f"mount options contain unsafe characters: {extra!r}")

    options: list[str] = list(BASE_OPTIONS[kind])
    seen = {opt.split("=")[0] for opt in options}
    for opt in [o.strip() for o in (extra or "").split(",") if o.strip()]:
        if opt in FORBIDDEN_OPTIONS:
            continue
        name = opt.split("=")[0]
        if name in seen:
            options = [o for o in options if o.split("=")[0] != name]
        options.append(opt)
        seen.add(name)
    if kind == "cifs" and credentials_file:
        if not OPTION_RE.match(credentials_file):
            raise InstallerError("credentials file path contains unsafe characters")
        options = [o for o in options if not o.startswith("credentials=")]
        options.append(f"credentials={credentials_file}")
    return ",".join(options)


def fstab_escape(path: str) -> str:
    """fstab octal-escapes whitespace in fields."""
    return (path.replace("\\", "\\134")
                .replace(" ", "\\040")
                .replace("\t", "\\011"))


def build_entry(mount: dict) -> str:
    kind = str(mount.get("type") or "")
    source = str(mount.get("source") or "").strip()
    target = str(mount.get("mountpoint") or "").strip()
    if not source or not target:
        raise InstallerError("mount entry needs both source and mountpoint")
    options = build_options(kind, str(mount.get("options") or ""),
                            str(mount.get("credentials_file") or ""))
    return (f"{fstab_escape(source)}\t{fstab_escape(target)}\t{kind}\t"
            f"{options}\t0\t0\t{TAG}")


def read_fstab(path: str = FSTAB) -> list[str]:
    try:
        return Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []


def has_entry_for(target: str, path: str = FSTAB) -> bool:
    """True when any fstab line - ours or not - already mounts *target*."""
    escaped = fstab_escape(os.path.normpath(target).rstrip("/"))
    for line in read_fstab(path):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        fields = stripped.split()
        if len(fields) >= 2 and fields[1].rstrip("/") == escaped:
            return True
    return False


def add_entries(mounts: list[dict], *, fstab_path: str = FSTAB, backups=None,
                dry_run: bool = False, logger=None) -> list[str]:
    """Append managed entries for mounts that do not have one. Idempotent."""
    from ..core.logging import get_logger
    logger = logger or get_logger()

    lines = read_fstab(fstab_path)
    added: list[str] = []
    for mount in mounts:
        if not mount.get("manage_fstab"):
            continue
        target = str(mount.get("mountpoint") or "").strip()
        if not target:
            continue
        if has_entry_for(target, fstab_path):
            logger.debug(f"fstab already has an entry for {target}")
            continue
        entry = build_entry(mount)
        lines.append(entry)
        added.append(entry)
        logger.info(f"fstab entry added for {target}")

    if added and not dry_run:
        write_file(fstab_path, "\n".join(lines) + "\n", mode=0o644,
                   backups=backups, dry_run=dry_run, logger=logger)
    elif added:
        logger.info(f"[dry-run] would add {len(added)} fstab entries")
    return added


def remove_managed_entries(*, fstab_path: str = FSTAB, backups=None,
                           dry_run: bool = False, logger=None) -> int:
    """Remove only the lines this installer added. Never touches anything else."""
    from ..core.logging import get_logger
    logger = logger or get_logger()
    lines = read_fstab(fstab_path)
    kept = [line for line in lines if TAG not in line]
    removed = len(lines) - len(kept)
    if removed and not dry_run:
        write_file(fstab_path, "\n".join(kept) + "\n", mode=0o644,
                   backups=backups, dry_run=dry_run, logger=logger)
    if removed:
        logger.info(f"removed {removed} managed fstab entries")
    return removed
