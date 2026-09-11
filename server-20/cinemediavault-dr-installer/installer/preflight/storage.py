"""Media path, mount and permission checks.

The rules here are the ones that protect an existing library. A media root is
only accepted when it is specific enough to be a library, reachable, and either
readable-and-writable or explicitly read-only for a library that never needs
writes. Nothing in the installer ever deletes or rewrites media.
"""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path

from ..config.schema import PathSafetyError, check_media_path
from ..core.fsops import free_bytes, human_bytes
from . import CheckResult, fail, ok, warn

#: Roots CineMediaVault only reads from. The rest must be writable.
READ_ONLY_ROOTS = {"movies", "tv", "music", "books", "comics"}
WRITABLE_ROOTS = {"comic_library", "games", "recordings"}

#: Free space each writable root should have before the installer is happy.
MIN_FREE_GB = {"recordings": 20, "comic_library": 2, "games": 1}

#: A directory holding more than this many entries at the top level is almost
#: certainly not a library root; it is more likely a home directory or a mount
#: parent. Warned, not refused, because very large flat libraries do exist.
SUSPICIOUS_ENTRY_COUNT = 5000


def checks(ctx) -> list[CheckResult]:
    results: list[CheckResult] = []
    roots = ctx.media_roots()

    if not roots:
        results.append(warn(
            "storage.none", "Media libraries",
            "No media locations were configured.",
            "CineMediaVault will install and start, but every library will be "
            "empty until you set paths on the Media step."))

    for name, path in roots.items():
        results.extend(_check_root(ctx, name, path))

    for index, mount in enumerate(ctx.get("media.mounts") or []):
        results.extend(_check_mount(ctx, index, mount))

    # Install-target writability. The target itself usually does not exist yet,
    # so the question is whether the nearest existing ancestor can be written -
    # that is the directory the installer will actually create into.
    for label, target in (
        ("install", ctx.layout.install_root),
        ("state", ctx.layout.state_root),
        ("config", ctx.layout.config_root),
    ):
        anchor = _nearest_existing(target)
        if os.geteuid() == 0 or os.access(anchor, os.W_OK):
            results.append(ok(f"storage.{label}", f"{label.title()} directory",
                              str(target)))
        else:
            results.append(fail(
                f"storage.{label}", f"{label.title()} directory",
                f"{target} cannot be created: {anchor} is not writable.",
                "Run the installer as root."))

    return results


def _check_root(ctx, name: str, raw: str) -> list[CheckResult]:
    check_id = f"storage.{name}"
    title = f"{name.replace('_', ' ').title()} library"
    results: list[CheckResult] = []

    try:
        path = Path(check_media_path(raw, label=name))
    except PathSafetyError as exc:
        return [fail(check_id, title, str(exc),
                     "Choose a dedicated directory, for example "
                     f"/srv/cinemediavault/{name}.")]

    if not path.exists():
        if ctx.get("media.create_missing"):
            parent = path.parent
            if not parent.exists():
                return [fail(
                    check_id, title,
                    f"{path} does not exist and neither does its parent {parent}.",
                    "Create or mount the parent directory first. The installer "
                    "will not create a deep tree it cannot verify.")]
            if not os.access(parent, os.W_OK) and os.geteuid() != 0:
                return [fail(check_id, title,
                             f"{parent} is not writable, so {path} cannot be created.",
                             "Fix the permissions or choose another location.")]
            return [warn(check_id, title,
                         f"{path} does not exist yet and will be created.",
                         "Nothing existing is touched.")]
        return [fail(
            check_id, title, f"{path} does not exist.",
            "Create or mount it first, or turn on 'create missing directories'.")]

    if not path.is_dir():
        return [fail(check_id, title, f"{path} exists but is not a directory.", "")]

    # Mount health: an unmounted mount point looks like an empty library, which
    # is the single most damaging failure mode for a scanner.
    mounted, source = _mount_info(ctx, path)
    entries = _count_entries(path)
    if mounted:
        results.append(ok(check_id + ".mount", f"{title} mount",
                          f"{path} is a mount point ({source})"))
    elif _looks_like_stale_mountpoint(path, entries):
        # A warning rather than a failure: an empty directory is genuinely
        # ambiguous - it is either an unmounted share or a library the operator
        # is about to fill. The hard refusal lives where it can be certain,
        # in the index refresh, which will not overwrite a populated catalogue
        # with the result of walking an empty directory.
        results.append(warn(
            check_id + ".mount", f"{title} mount",
            f"{path} is empty and sits where a network share is usually "
            f"mounted.",
            "If media should already be here, mount the share before "
            "installing. Scheduled scans refuse to rebuild an index from an "
            "empty directory, so an unmounted share cannot erase your "
            "catalogue - but the library will show nothing until it is "
            "mounted."))

    # Readability.
    if not os.access(path, os.R_OK | os.X_OK):
        results.append(fail(check_id, title, f"{path} is not readable.",
                            "Grant read access to the CineMediaVault service user."))
        return results

    # Writability, where it matters.
    needs_write = name in WRITABLE_ROOTS
    writable, why = _probe_write(path)
    if needs_write and not writable:
        results.append(fail(
            check_id, title, f"{path} is not writable ({why}).",
            f"The {name.replace('_', ' ')} location must be writable by the "
            f"CineMediaVault service user."))
    elif not needs_write and not writable:
        results.append(ok(check_id, title,
                          f"{path} readable (read-only is fine for {name})",
                          entries=entries))
    else:
        results.append(ok(check_id, title, f"{path} readable and writable",
                          entries=entries))

    # Free space where the installer or the DVR will write.
    if name in MIN_FREE_GB:
        try:
            free = free_bytes(path)
            need = MIN_FREE_GB[name] * (1024 ** 3)
            if free < need:
                severity = fail if name == "recordings" else warn
                results.append(severity(
                    check_id + ".space", f"{title} free space",
                    f"{human_bytes(free)} free; {MIN_FREE_GB[name]} GB expected.",
                    "Recordings stop when the destination fills. Free space or "
                    "choose a larger volume."
                    if name == "recordings" else "Generated output needs room."))
            else:
                results.append(ok(check_id + ".space", f"{title} free space",
                                  human_bytes(free)))
        except OSError as exc:
            results.append(warn(check_id + ".space", f"{title} free space",
                                f"could not check: {exc}"))

    # DVR-specific free space against the configured floor.
    if name == "recordings":
        floor_gb = int(ctx.get("dvr.min_free_gb") or 0)
        try:
            free_gb = free_bytes(path) / (1024 ** 3)
            if floor_gb and free_gb < floor_gb:
                results.append(warn(
                    check_id + ".floor", "DVR free-space floor",
                    f"{free_gb:.1f} GB free is already below the configured "
                    f"{floor_gb} GB floor, so recording would be refused "
                    f"immediately.",
                    "Lower the floor or free space before enabling the DVR."))
        except OSError:
            pass

    if entries > SUSPICIOUS_ENTRY_COUNT:
        results.append(warn(
            check_id + ".size", f"{title} contents",
            f"{entries} entries directly under {path}.",
            "That is unusual for a library root. Confirm this is the library "
            "itself and not a parent directory - scanning a parent is slow and "
            "produces poor matches."))

    return results


def _check_mount(ctx, index: int, mount: dict) -> list[CheckResult]:
    check_id = f"storage.mount{index}"
    if not isinstance(mount, dict):
        return [fail(check_id, f"Mount {index}", "entry is not a mapping", "")]
    source = str(mount.get("source") or "")
    point = str(mount.get("mountpoint") or "")
    kind = str(mount.get("type") or "")
    title = f"{kind.upper()} mount {source}"

    results: list[CheckResult] = []
    already, actual = _mount_info(ctx, Path(point)) if point else (False, "")
    if already:
        results.append(ok(check_id, title, f"already mounted at {point} ({actual})"))
        return results

    helper = {"nfs": "mount.nfs", "cifs": "mount.cifs"}.get(kind)
    if helper and not ctx.runner.has(helper):
        pkg = {"nfs": "nfs-common", "cifs": "cifs-utils"}[kind]
        results.append(warn(
            check_id, title, f"{helper} is not installed.",
            f"The installer will install {pkg}."))

    creds = str(mount.get("credentials_file") or "")
    if kind == "cifs" and creds:
        cred_path = Path(creds)
        if not cred_path.is_file():
            results.append(fail(
                check_id + ".creds", f"{title} credentials",
                f"{creds} does not exist.",
                "Create it with 'username=' and 'password=' lines, mode 0600, "
                "before installing. The installer never writes share passwords."))
        else:
            mode = stat.S_IMODE(cred_path.stat().st_mode)
            if mode & 0o077:
                results.append(warn(
                    check_id + ".creds", f"{title} credentials",
                    f"{creds} is mode {mode:04o}; it is readable by other accounts.",
                    f"Run: chmod 600 {creds}"))
            else:
                results.append(ok(check_id + ".creds", f"{title} credentials",
                                  "present and private"))
    elif kind == "cifs" and not creds:
        results.append(warn(
            check_id + ".creds", f"{title} credentials",
            "no credentials file configured.",
            "Guest access only works on shares that allow it."))

    results.append(warn(
        check_id, title,
        f"{source} is not mounted at {point} yet.",
        "The installer can add an fstab entry and mount it, if 'manage fstab' "
        "is enabled for this mount. Otherwise mount it first."))
    return results


def _nearest_existing(path: Path) -> Path:
    """The closest ancestor of *path* that exists."""
    current = path
    while not current.exists() and current != current.parent:
        current = current.parent
    return current


def _mount_info(ctx, path: Path) -> tuple[bool, str]:
    """Return (is_mount_point, source). Uses findmnt, falls back to st_dev."""
    result = ctx.runner.probe(["findmnt", "-n", "-o", "SOURCE,FSTYPE", "--target", str(path)])
    if result.returncode == 0 and result.stdout.strip():
        fields = result.stdout.strip().split()
        source = fields[0] if fields else ""
        mount_result = ctx.runner.probe(["findmnt", "-n", "-o", "TARGET", "--target", str(path)])
        target = mount_result.stdout.strip()
        return (target == str(path), source)
    try:
        return (os.path.ismount(path), "")
    except OSError:
        return (False, "")


def _count_entries(path: Path, limit: int = 20000) -> int:
    try:
        count = 0
        with os.scandir(path) as it:
            for _ in it:
                count += 1
                if count >= limit:
                    break
        return count
    except OSError:
        return -1


def _looks_like_stale_mountpoint(path: Path, entries: int) -> bool:
    """An empty directory whose name suggests a share is a likely stale mount."""
    if entries != 0:
        return False
    hints = ("mnt", "media", "nfs", "smb", "cifs", "share", "nas")
    parts = [p.lower() for p in path.parts]
    return any(hint in part for part in parts for hint in hints)


def _probe_write(path: Path) -> tuple[bool, str]:
    """Non-destructive writability probe: creates and removes one temp file."""
    try:
        fd, name = tempfile.mkstemp(dir=str(path), prefix=".cmv-write-probe-")
        os.close(fd)
        os.unlink(name)
        return True, ""
    except OSError as exc:
        return False, exc.strerror or str(exc)
