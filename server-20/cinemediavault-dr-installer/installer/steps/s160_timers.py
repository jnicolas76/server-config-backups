"""Enable the services and timers that should run automatically.

Enabling is separated from installing so that ``--dry-run`` and a
configuration-only re-apply never start anything, and so that a failed unit is
visible as an explicit result rather than as a silent omission.
"""

from __future__ import annotations

from .base import Step, StepResult


class EnableUnits(Step):
    id = "timers"
    title = "Service activation"
    description = "Enable the CineMediaVault service, timers and module services."
    requires = ("systemd",)

    def fingerprint_inputs(self, ctx):
        return [self._wanted(ctx)]

    def _wanted(self, ctx) -> list[str]:
        units = ["cinemediavault.service"]
        if ctx.get("ops.health_check_enabled"):
            units.append("cinemediavault-health.timer")
        units.append("cinemediavault-refresh.timer")
        units.append("cinemediavault-backup.timer")
        if ctx.get("modules.movies") or ctx.get("modules.tv"):
            units.append("cinemediavault-metadata.timer")
        if ctx.get("modules.tv"):
            units.append("cinemediavault-thumbnails.timer")
        if ctx.get("modules.comics"):
            units.append("cinemediavault-module@comics.service")
        for game in ctx.selected_games():
            units.append(f"cinemediavault-module@{game}.service")
        if ctx.get("modules.bookvault"):
            units.append("cinemediavault-bookvault.service")
        # The Whisper worker is installed but only enabled when the operator
        # asked for it: the first catalogue pass runs for days.
        if ctx.get("subtitles.whisper_enabled") and \
                ctx.get("subtitles.whisper_start_enabled"):
            units.append("cinemediavault-subtitles.service")
        if ctx.get("promo_channel.enabled"):
            units.append("cinemediavault-promo.timer")
        return units

    def preview(self, ctx) -> str:
        return "Enable: " + ", ".join(self._wanted(ctx))

    def run(self, ctx) -> StepResult:
        wanted = self._wanted(ctx)
        enabled: list[str] = []
        warnings: list[str] = []

        for unit in wanted:
            if ctx.dry_run:
                ctx.logger.info(f"[dry-run] would enable {unit}")
                continue
            if ctx.runner.systemd_unit_enabled(unit):
                ctx.logger.debug(f"{unit} already enabled")
                continue
            result = ctx.runner.systemctl("enable", unit, check=False)
            if result.returncode == 0:
                enabled.append(unit)
            else:
                warnings.append(f"could not enable {unit}: "
                                f"{(result.stderr or '').strip()[:200]}")

        # Anything left over from a previous configuration is disabled, not
        # deleted: an operator who turns Music off should not have a stale timer
        # firing, but should still find the unit if they turn it back on.
        stale = self._stale_units(ctx, wanted)
        for unit in stale:
            if ctx.dry_run:
                ctx.logger.info(f"[dry-run] would disable {unit}")
                continue
            ctx.runner.systemctl("disable", "--now", unit, check=False)

        outcome = StepResult(
            changed=bool(enabled or stale),
            summary=f"{len(enabled)} unit(s) enabled, {len(stale)} disabled",
            data={"enabled": enabled, "disabled": stale},
        )
        for message in warnings:
            outcome.warn(message)
        return outcome

    def _stale_units(self, ctx, wanted: list[str]) -> list[str]:
        if ctx.dry_run:
            return []
        candidates = [
            "cinemediavault-health.timer", "cinemediavault-refresh.timer",
            "cinemediavault-backup.timer", "cinemediavault-metadata.timer",
            "cinemediavault-thumbnails.timer", "cinemediavault-bookvault.service",
            "cinemediavault-subtitles.service", "cinemediavault-module@comics.service",
            "cinemediavault-promo.timer",
        ]
        from .s100_modules import GAME_MODULES
        candidates.extend(f"cinemediavault-module@{g}.service" for g in GAME_MODULES)
        return [unit for unit in candidates
                if unit not in wanted and ctx.runner.systemd_unit_enabled(unit)]


def steps():
    return [EnableUnits()]
