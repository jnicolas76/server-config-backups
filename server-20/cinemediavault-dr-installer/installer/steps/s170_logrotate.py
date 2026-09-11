"""Install log rotation for the CineMediaVault log tree."""

from __future__ import annotations

from pathlib import Path

from ..core.fsops import write_file
from ..templates import render_template
from .base import Step, StepResult

LOGROTATE_DIR = Path("/etc/logrotate.d")


class InstallLogrotate(Step):
    id = "logrotate"
    title = "Log rotation"
    description = "Rotate CineMediaVault logs daily and keep the configured history."
    depends_on = ("ops.log_retain_days", "paths.log_root", "paths.service_user")
    requires = ("directories",)

    def preview(self, ctx) -> str:
        return (f"Rotate {ctx.layout.log_root}/*.log daily, keeping "
                f"{ctx.get('ops.log_retain_days')} days.")

    def run(self, ctx) -> StepResult:
        if not LOGROTATE_DIR.is_dir() and not ctx.dry_run:
            return StepResult(summary="logrotate is not installed").warn(
                "logrotate.d was not found, so logs will not be rotated. "
                "Install the logrotate package and run "
                "`cinevaultctl repair --only logrotate`.")

        content = render_template(ctx.package_root, "logrotate/cinemediavault", {
            "log_root": str(ctx.layout.log_root),
            "retain_days": int(ctx.get("ops.log_retain_days")),
            "service_user": ctx.service_user,
            "service_group": ctx.service_group,
        })
        changed = write_file(LOGROTATE_DIR / "cinemediavault", content, mode=0o644,
                             backups=ctx.backups, dry_run=ctx.dry_run,
                             logger=ctx.logger)

        # A broken logrotate file silently stops rotation for the whole system,
        # so it is validated rather than trusted.
        if changed and not ctx.dry_run and ctx.runner.has("logrotate"):
            check = ctx.runner.run(
                ["logrotate", "--debug", str(LOGROTATE_DIR / "cinemediavault")],
                check=False, timeout=60)
            if check.returncode != 0:
                return StepResult(changed=changed,
                                  summary="logrotate configuration written").warn(
                    "logrotate reported a problem with the generated "
                    "configuration: " +
                    (check.stderr or check.stdout).strip().splitlines()[-1][:200])

        return StepResult(changed=changed, summary="log rotation configured")


def steps():
    return [InstallLogrotate()]
