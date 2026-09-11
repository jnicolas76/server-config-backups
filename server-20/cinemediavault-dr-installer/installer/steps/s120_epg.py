"""The private extended-EPG collector.

Design
------
The tuner's own guide stays authoritative. The collector is an isolated Docker
Compose project running the upstream `iptv-org/epg` grabber, which publishes a
read-only XMLTV file on a private address. CineMediaVault merges that file
*behind* the tuner's horizon and only when every overlapping programme title and
time still agrees. A missing, stale, malformed or shifted feed therefore changes
nothing: the guide simply stops at the tuner's own horizon.

Why a container rather than a systemd service: the grabber is a Node application
with a large dependency tree that is rebuilt from upstream source. Keeping it in
its own image means a grabber update cannot disturb the Python application, and
the memory ceiling that the grabber genuinely needs is enforced by the runtime
rather than by hope.

The collector is bound to a private address, never ``0.0.0.0``, and is never
published to the internet.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from ..core.errors import StepError
from ..core.fsops import ensure_dir, write_file
from ..templates import render_template
from .base import Step, StepResult

#: Verified upstream ceiling. Asking for more silently returns duplicate days.
MAX_HORIZON_DAYS = 14


class InstallEPGCollector(Step):
    id = "epg"
    title = "Extended guide collector"
    description = ("Build and start the private iptv-org/epg collector that "
                   "extends the guide beyond the tuner's own horizon.")
    depends_on = ("epg.mode", "epg.collector_bind_address", "epg.collector_port",
                  "epg.horizon_days", "epg.refresh_time", "epg.request_delay_ms",
                  "epg.max_connections", "epg.request_timeout_ms",
                  "epg.memory_limit_mb", "epg.cpu_limit", "epg.channel_map",
                  "deployment.timezone")
    requires = ("packages.docker", "config")

    def applies(self, ctx) -> bool:
        return ctx.get("epg.mode") == "extended"

    def preview(self, ctx) -> str:
        return (f"Build the collector image from the upstream iptv-org/epg source "
                f"and serve XMLTV on "
                f"http://{ctx.get('epg.collector_bind_address')}:"
                f"{ctx.get('epg.collector_port')}/guide.xml, collecting daily at "
                f"{ctx.get('epg.refresh_time')} "
                f"{ctx.get('deployment.timezone')}.")

    def run(self, ctx) -> StepResult:
        project = ctx.layout.compose_dir / "epg"
        ensure_dir(project, mode=0o750, user="root", group=ctx.service_group,
                   dry_run=ctx.dry_run, logger=ctx.logger)
        ensure_dir(project / "build", mode=0o750, user="root",
                   group=ctx.service_group, dry_run=ctx.dry_run, logger=ctx.logger)
        # The grabber writes here; it runs as root inside the container but the
        # bind mount is what the host sees.
        ensure_dir(project / "public", mode=0o755, user="root",
                   group=ctx.service_group, dry_run=ctx.dry_run, logger=ctx.logger)

        hours, _, minutes = (ctx.get("epg.refresh_time") or "03:20").partition(":")
        horizon = min(int(ctx.get("epg.horizon_days") or 14), MAX_HORIZON_DAYS)

        variables = {
            "bind_address": ctx.get("epg.collector_bind_address"),
            "port": int(ctx.get("epg.collector_port")),
            "timezone": ctx.get("deployment.timezone"),
            "days": horizon,
            "max_connections": int(ctx.get("epg.max_connections")),
            "delay_ms": int(ctx.get("epg.request_delay_ms")),
            "timeout_ms": int(ctx.get("epg.request_timeout_ms")),
            "cron_schedule": f"{int(minutes)} {int(hours)} * * *",
            "memory_limit_mb": int(ctx.get("epg.memory_limit_mb")),
            "cpu_limit": float(ctx.get("epg.cpu_limit")),
            # Node sizes its heap from the container limit; without an explicit
            # ceiling the grabber dies part-way through a large collection.
            "node_heap_mb": max(1024, int(ctx.get("epg.memory_limit_mb")) - 1024),
        }

        changed = False
        for name, target, mode in (
            ("compose/epg/docker-compose.yml", project / "docker-compose.yml", 0o640),
            ("compose/epg/build/Dockerfile", project / "build" / "Dockerfile", 0o644),
            ("compose/epg/build/pm2.config.js", project / "build" / "pm2.config.js", 0o644),
            ("compose/epg/build/tvpassport-enrich.config.js",
             project / "build" / "tvpassport-enrich.config.js", 0o644),
        ):
            changed |= write_file(
                target, render_template(ctx.package_root, name, variables),
                mode=mode, user="root", group=ctx.service_group,
                backups=ctx.backups, dry_run=ctx.dry_run, logger=ctx.logger)

        changed |= self._install_channel_map(ctx, project)
        warnings: list[str] = []
        started = self._bring_up(ctx, project, warnings)

        result = StepResult(
            changed=changed or started,
            summary=("collector configured"
                     + (" and started" if started else " (not started)")),
            data={"project": str(project),
                  "feed": f"http://{variables['bind_address']}:{variables['port']}/guide.xml"},
        )
        for message in warnings:
            result.warn(message)
        return result

    # ------------------------------------------------------------------
    def _install_channel_map(self, ctx, project: Path) -> bool:
        """Copy the operator's verified channels.xml into the project."""
        source = (ctx.get("epg.channel_map") or "").strip()
        target = project / "public" / "channels.xml"
        if not source:
            if not target.exists() and not ctx.dry_run:
                # An empty map is valid and safe: the collector produces an empty
                # guide, and the merge falls back to the tuner's own data.
                write_file(target,
                           '<?xml version="1.0" encoding="UTF-8"?>\n'
                           '<!-- No verified channel mapping was supplied.\n'
                           '     The collector will produce an empty guide and\n'
                           '     CineMediaVault will use the tuner guide only.\n'
                           '     Add mappings with `cinevaultctl epg map`. -->\n'
                           "<channels>\n</channels>\n",
                           mode=0o644, backups=ctx.backups, logger=ctx.logger)
            return False
        path = Path(source)
        if not path.is_file():
            raise StepError(f"EPG channel map not found: {path}", step=self.id,
                            recoverable=False)
        content = path.read_bytes()
        if b"<channels" not in content:
            raise StepError(
                f"{path} does not look like an iptv-org channels.xml "
                f"(no <channels> element)", step=self.id, recoverable=False)
        return write_file(target, content, mode=0o644, user="root",
                          group=ctx.service_group, backups=ctx.backups,
                          dry_run=ctx.dry_run, logger=ctx.logger)

    def _bring_up(self, ctx, project: Path, warnings: list[str]) -> bool:
        if ctx.dry_run:
            ctx.logger.info("[dry-run] would build and start the EPG collector")
            return False
        if not ctx.runner.has("docker"):
            warnings.append("Docker is not available; the collector was configured "
                            "but not started.")
            return False
        if ctx.offline:
            warnings.append("Offline mode: the collector image was not built. "
                            "Run `cinevaultctl epg rebuild` when the host has "
                            "internet access.")
            return False

        ctx.logger.info("building the EPG collector image (this clones the "
                        "upstream iptv-org/epg source and can take several minutes)")
        build = ctx.runner.run(
            ["docker", "compose", "-f", str(project / "docker-compose.yml"),
             "build", "--pull"],
            check=False, timeout=1800)
        if build.returncode != 0:
            warnings.append(
                "The collector image did not build. The tuner guide continues to "
                "work; run `cinevaultctl epg rebuild` to retry. Last error: "
                + (build.stderr or build.stdout).strip().splitlines()[-1][:200])
            return False

        up = ctx.runner.run(
            ["docker", "compose", "-f", str(project / "docker-compose.yml"),
             "up", "-d"],
            check=False, timeout=300)
        if up.returncode != 0:
            warnings.append("The collector image built but the container did not "
                            "start. Check `docker compose logs` in " + str(project))
            return False

        warnings.append(
            "The first collection walks the full requested horizon at the "
            "configured pacing and typically takes 45-90 minutes. The guide grid "
            "keeps using the tuner's own data until it finishes.")
        return True


class RemoveEPGCollector(Step):
    id = "epg.remove"
    title = "Extended guide collector (disabled)"
    description = "Stop the collector when the extended guide is turned off."
    depends_on = ("epg.mode",)
    requires = ("config",)

    def applies(self, ctx) -> bool:
        if ctx.get("epg.mode") == "extended":
            return False
        return (ctx.layout.compose_dir / "epg" / "docker-compose.yml").is_file()

    def run(self, ctx) -> StepResult:
        project = ctx.layout.compose_dir / "epg"
        if ctx.dry_run:
            return StepResult(summary="would stop the EPG collector")
        if not ctx.runner.has("docker"):
            return StepResult(summary="docker unavailable; nothing to stop")
        ctx.runner.run(
            ["docker", "compose", "-f", str(project / "docker-compose.yml"), "down"],
            check=False, timeout=180)
        return StepResult(changed=True,
                          summary="EPG collector stopped; collected guide data kept")


def steps():
    return [InstallEPGCollector(), RemoveEPGCollector()]
