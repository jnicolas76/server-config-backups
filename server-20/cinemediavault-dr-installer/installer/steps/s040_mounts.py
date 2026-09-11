"""Validate, and optionally add, network mounts."""

from __future__ import annotations

from pathlib import Path

from ..core.errors import StepError
from ..core.fsops import ensure_dir
from ..discovery import mounts as mount_tools
from .base import Step, StepResult


class ConfigureMounts(Step):
    id = "mounts"
    title = "Network mounts"
    description = ("Create mount points, add managed /etc/fstab entries for "
                   "mounts marked 'manage fstab', and mount them.")
    depends_on = ("media.mounts",)
    requires = ("directories",)

    def applies(self, ctx) -> bool:
        return bool(ctx.get("media.mounts"))

    def preview(self, ctx) -> str:
        entries = ctx.get("media.mounts") or []
        managed = [m for m in entries if m.get("manage_fstab")]
        parts = [f"{len(entries)} mount(s) configured"]
        if managed:
            parts.append(f"{len(managed)} will get an /etc/fstab entry")
        return "; ".join(parts) + "."

    def run(self, ctx) -> StepResult:
        entries = ctx.get("media.mounts") or []
        created_points = 0
        mounted: list[str] = []
        warnings: list[str] = []

        for mount in entries:
            target = str(mount.get("mountpoint") or "").strip()
            if not target:
                continue
            if not Path(target).exists():
                ensure_dir(target, mode=0o755, dry_run=ctx.dry_run, logger=ctx.logger)
                created_points += 1

        added = mount_tools.add_entries(
            entries, backups=ctx.backups, dry_run=ctx.dry_run, logger=ctx.logger)

        if added and not ctx.dry_run:
            # systemd needs to see the new automount units before they resolve.
            ctx.runner.systemctl("daemon-reload", check=False)

        for mount in entries:
            target = str(mount.get("mountpoint") or "").strip()
            if not target:
                continue
            if ctx.dry_run:
                continue
            if mount_tools.is_mounted(target, ctx.runner):
                mounted.append(target)
                continue
            if not mount.get("manage_fstab"):
                warnings.append(
                    f"{target} is not mounted and 'manage fstab' is off for it. "
                    f"Mount it before scanning, or the library will look empty.")
                continue
            result = ctx.runner.run(["mount", target], check=False, timeout=90)
            if result.returncode == 0:
                mounted.append(target)
            else:
                tail = (result.stderr or result.stdout or "").strip().splitlines()
                warnings.append(
                    f"could not mount {target}: {tail[-1] if tail else 'unknown error'}")

        outcome = StepResult(
            changed=bool(created_points or added),
            summary=(f"{created_points} mount point(s) created, {len(added)} fstab "
                     f"entries added, {len(mounted)} mounted"),
            data={"added_fstab": added, "mounted": mounted},
        )
        for message in warnings:
            outcome.warn(message)
        return outcome


def steps():
    return [ConfigureMounts()]
