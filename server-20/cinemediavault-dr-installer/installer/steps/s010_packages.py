"""Install the operating-system packages this configuration needs."""

from __future__ import annotations

from ..preflight.deps import required_packages
from .base import Step, StepResult


class InstallPackages(Step):
    id = "packages"
    title = "System packages"
    description = "Install only the apt packages the selected components require."
    depends_on = ("modules.bookvault", "modules.comics", "modules.creators",
                  "media.mounts", "network.configure_firewall", "epg.mode",
                  "subtitles.whisper_enabled", "promo_channel.enabled")

    def preview(self, ctx) -> str:
        packages = required_packages(ctx)
        missing = [p for p in packages if not ctx.runner.apt_installed(p)]
        if not missing:
            return f"All {len(packages)} required packages are already installed."
        return "Install: " + ", ".join(missing)

    def run(self, ctx) -> StepResult:
        packages = required_packages(ctx)
        ctx.logger.step(self.id, f"{len(packages)} packages required")
        installed = ctx.runner.apt_install(packages)
        if not installed:
            return StepResult(changed=False,
                              summary="all required packages already present",
                              data={"required": packages})
        return StepResult(
            changed=True,
            summary=f"installed {len(installed)} packages",
            data={"installed": installed, "required": packages},
        )


class EnableDocker(Step):
    id = "packages.docker"
    title = "Docker runtime"
    description = "Enable the Docker service used by the EPG collector."
    depends_on = ("epg.mode",)
    requires = ("packages",)

    def applies(self, ctx) -> bool:
        return ctx.get("epg.mode") == "extended"

    def run(self, ctx) -> StepResult:
        if not ctx.runner.has("docker") and not ctx.dry_run:
            return StepResult(summary="docker is not installed").warn(
                "Docker is unavailable, so the extended EPG collector cannot run.")
        if ctx.runner.systemd_unit_active("docker.service"):
            return StepResult(changed=False, summary="docker already running")
        ctx.runner.enable_now("docker.service")
        return StepResult(changed=True, summary="docker enabled and started")


def steps():
    return [InstallPackages(), EnableDocker()]
