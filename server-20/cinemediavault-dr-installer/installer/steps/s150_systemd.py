"""Render and install the systemd units.

Every unit is generated from a template so that the sandboxing directives, the
resource ceilings and the exact media paths always match the configuration that
produced them. A unit is only written when its content actually changes, so a
repeat run does not churn ``daemon-reload``.
"""

from __future__ import annotations

from pathlib import Path

from ..core.fsops import ensure_dir, write_file
from ..templates import render_template
from .base import Step, StepResult

SYSTEMD_DIR = Path("/etc/systemd/system")


def _media_path_lists(ctx) -> tuple[str, str]:
    """Split the configured media roots into writable and read-only sets.

    This is what turns ``ProtectSystem=strict`` from an obstacle into real
    protection: the service can write exactly the recordings, generated library
    and ROM directories, and can only read everything else.
    """
    writable_keys = ("media.recordings_root", "media.comic_library_root",
                     "media.games_root")
    readable_keys = ("media.movies_root", "media.tv_root", "media.music_root",
                     "media.books_root", "media.comics_root")
    writable = [str(ctx.get(k)).strip() for k in writable_keys if (ctx.get(k) or "").strip()]
    readable = [str(ctx.get(k)).strip() for k in readable_keys if (ctx.get(k) or "").strip()]
    return " ".join(writable), " ".join(readable)


def base_variables(ctx) -> dict:
    writable_media, readable_media = _media_path_lists(ctx)
    layout = ctx.layout
    return {
        "instance_name": ctx.get("meta.instance_name") or "CineMediaVault",
        "install_root": str(layout.install_root),
        "app_dir": str(layout.app_dir),
        "scripts_dir": str(layout.scripts_dir),
        "modules_dir": str(layout.modules_dir),
        "config_root": str(layout.config_root),
        "state_root": str(layout.state_root),
        "log_root": str(layout.log_root),
        "cache_root": str(layout.cache_root),
        "env_file": str(layout.env_file),
        "secrets_file": str(layout.secrets_file),
        "service_user": ctx.service_user,
        "service_group": ctx.service_group,
        "listen_address": ctx.get("network.listen_address"),
        "port": (ctx.get("network.https_port") if ctx.get("network.tls_mode") != "none"
                 else ctx.get("network.http_port")),
        "memory_limit_mb": int(ctx.get("resources.app_memory_limit_mb")),
        "cpu_quota": int(ctx.get("resources.app_cpu_quota_percent") or 0),
        "writable_media": writable_media,
        "readable_media": readable_media,
        "has_mount_units": bool(ctx.get("media.mounts")),
        # Hardware transcoding needs the render node, so PrivateDevices must be
        # relaxed only in that case.
        "private_devices": (
            "false" if (ctx.get("transcode.hls_enabled")
                        and ctx.get("transcode.hls_encoder") != "cpu")
            else "true"),
    }


class InstallSystemdUnits(Step):
    id = "systemd"
    title = "systemd services"
    description = "Install the CineMediaVault service and its auxiliary units."
    requires = ("config", "payload", "tls")

    def fingerprint_inputs(self, ctx):
        return [ctx.config_hash]

    def preview(self, ctx) -> str:
        return f"Install {len(self._units(ctx))} systemd unit(s) into {SYSTEMD_DIR}."

    def _units(self, ctx) -> list[tuple[str, str, dict]]:
        base = base_variables(ctx)
        units: list[tuple[str, str, dict]] = [
            ("cinemediavault.service", "cinemediavault.service", base),
        ]

        if ctx.get("ops.health_check_enabled"):
            health = dict(base)
            scheme = "https" if ctx.get("network.tls_mode") != "none" else "http"
            health["health_url"] = f"{scheme}://127.0.0.1:{base['port']}/login"
            health["failure_threshold"] = int(ctx.get("ops.health_failure_threshold"))
            health["restart_cooldown"] = int(
                ctx.get("ops.health_restart_cooldown_seconds"))
            units.append(("cinemediavault-health.service",
                          "cinemediavault-health.service", health))
            units.append(("cinemediavault-health.timer",
                          "cinemediavault-health.timer", health))

        refresh = dict(base)
        refresh["movies"] = bool(ctx.get("modules.movies"))
        refresh["tv"] = bool(ctx.get("modules.tv"))
        refresh["refresh_calendar"] = _cron_to_calendar(
            ctx.get("metadata.refresh_cron") or "*/15 * * * *")
        units.append(("cinemediavault-refresh.service",
                      "cinemediavault-refresh.service", refresh))
        units.append(("cinemediavault-refresh.timer",
                      "cinemediavault-refresh.timer", refresh))

        backup = dict(base)
        backup["backup_time"] = ctx.get("ops.db_backup_time") or "01:00"
        units.append(("cinemediavault-backup.service",
                      "cinemediavault-backup.service", backup))
        units.append(("cinemediavault-backup.timer",
                      "cinemediavault-backup.timer", backup))

        if ctx.get("modules.movies") or ctx.get("modules.tv"):
            metadata = dict(base)
            metadata["movies"] = bool(ctx.get("modules.movies"))
            metadata["tv"] = bool(ctx.get("modules.tv"))
            units.append(("cinemediavault-metadata.service",
                          "cinemediavault-metadata.service", metadata))
            units.append(("cinemediavault-metadata.timer",
                          "cinemediavault-metadata.timer", metadata))

        if ctx.get("modules.tv"):
            thumbs = dict(base)
            thumbs["thumbnail_limit"] = 300
            # Thumbnail generation is pure background work; half a core keeps it
            # from competing with playback on a small VM.
            thumbs["thumbnail_cpu_quota"] = max(25, min(100, (ctx.facts.cpu_count or 2) * 25))
            units.append(("cinemediavault-thumbnails.service",
                          "cinemediavault-thumbnails.service", thumbs))
            units.append(("cinemediavault-thumbnails.timer",
                          "cinemediavault-thumbnails.timer", thumbs))

        if ctx.get("modules.comics") or ctx.selected_games():
            units.append(("cinemediavault-module@.service",
                          "cinemediavault-module@.service", base))

        if ctx.get("modules.bookvault"):
            book = dict(base)
            book["port"] = int(ctx.get("modules.bookvault_port"))
            book["book_root"] = ctx.get("media.books_root")
            book["bind_address"] = ctx.get("network.listen_address")
            units.append(("cinemediavault-bookvault.service",
                          "cinemediavault-bookvault.service", book))

        if ctx.get("promo_channel.enabled"):
            promo = dict(base)
            promo["promo_venv"] = str(ctx.layout.promo_dir / "venv")
            promo["promo_reels_dir"] = str(ctx.layout.promo_dir / "reels")
            promo["promo_model"] = ctx.get("promo_channel.model_path")
            promo["promo_voices"] = ctx.get("promo_channel.voices_path")
            promo["promo_minutes"] = int(ctx.get("promo_channel.minutes"))
            promo["promo_cpu_quota"] = int(ctx.get("promo_channel.cpu_quota_percent"))
            promo["promo_memory_mb"] = int(ctx.get("promo_channel.memory_limit_mb"))
            promo["promo_calendar"] = ctx.get("promo_channel.schedule_calendar")
            units.append(("cinemediavault-promo.service",
                          "cinemediavault-promo.service", promo))
            units.append(("cinemediavault-promo.timer",
                          "cinemediavault-promo.timer", promo))

        if ctx.get("subtitles.whisper_enabled"):
            whisper = dict(base)
            whisper["whisper_model"] = ctx.get("subtitles.whisper_model")
            whisper["whisper_device"] = ctx.get("subtitles.whisper_device")
            whisper["whisper_languages"] = ",".join(
                ctx.get("subtitles.languages") or ["en"])
            whisper["whisper_workers"] = int(ctx.get("subtitles.whisper_workers"))
            whisper["whisper_cpu_quota"] = int(
                ctx.get("subtitles.whisper_cpu_quota_percent"))
            whisper["whisper_memory_mb"] = int(
                ctx.get("subtitles.whisper_memory_limit_mb"))
            units.append(("cinemediavault-subtitles.service",
                          "cinemediavault-subtitles.service", whisper))

        return units

    def run(self, ctx) -> StepResult:
        written: list[str] = []
        for template_name, unit_name, variables in self._units(ctx):
            content = render_template(ctx.package_root, f"systemd/{template_name}",
                                      variables)
            if write_file(SYSTEMD_DIR / unit_name, content, mode=0o644,
                          backups=ctx.backups, dry_run=ctx.dry_run,
                          logger=ctx.logger):
                written.append(unit_name)

        changed = self._write_module_envs(ctx) or bool(written)
        changed |= self._write_scripts(ctx)

        if written and not ctx.dry_run:
            ctx.runner.daemon_reload()

        return StepResult(changed=changed,
                          summary=f"{len(written)} unit(s) written or updated",
                          data={"units": written})

    def _write_module_envs(self, ctx) -> bool:
        """One environment file per static module instance."""
        directory = ctx.layout.config_root / "modules"
        ensure_dir(directory, mode=0o750, user="root", group=ctx.service_group,
                   dry_run=ctx.dry_run, logger=ctx.logger)
        changed = False
        bind = ctx.get("network.listen_address")

        if ctx.get("modules.comics"):
            changed |= write_file(
                directory / "comics.env",
                render_template(ctx.package_root, "env/module.env", {
                    "module_id": "comics",
                    "module_root": ctx.get("media.comic_library_root")
                                   or ctx.get("media.comics_root"),
                    "bind_address": bind,
                    "port": int(ctx.get("modules.comics_port")),
                }),
                mode=0o640, user="root", group=ctx.service_group,
                backups=ctx.backups, dry_run=ctx.dry_run, logger=ctx.logger)

        base_port = int(ctx.get("modules.game_port_base") or 8090)
        for index, game in enumerate(ctx.selected_games()):
            changed |= write_file(
                directory / f"{game}.env",
                render_template(ctx.package_root, "env/module.env", {
                    "module_id": game,
                    "module_root": str(ctx.layout.modules_dir / game),
                    "bind_address": bind,
                    "port": base_port + index,
                }),
                mode=0o640, user="root", group=ctx.service_group,
                backups=ctx.backups, dry_run=ctx.dry_run, logger=ctx.logger)
        return changed

    def _write_scripts(self, ctx) -> bool:
        """Render the health and refresh helper scripts."""
        base = base_variables(ctx)
        scheme = "https" if ctx.get("network.tls_mode") != "none" else "http"
        changed = False

        health = dict(base)
        health["health_url"] = f"{scheme}://127.0.0.1:{base['port']}/login"
        health["failure_threshold"] = int(ctx.get("ops.health_failure_threshold"))
        health["restart_cooldown"] = int(ctx.get("ops.health_restart_cooldown_seconds"))
        changed |= write_file(
            ctx.layout.scripts_dir / "cinemediavault-health.sh",
            render_template(ctx.package_root, "scripts/cinemediavault-health.sh", health),
            mode=0o755, user="root", group=ctx.service_group,
            backups=ctx.backups, dry_run=ctx.dry_run, logger=ctx.logger)

        refresh = dict(base)
        refresh["movies"] = bool(ctx.get("modules.movies"))
        refresh["tv"] = bool(ctx.get("modules.tv"))
        changed |= write_file(
            ctx.layout.scripts_dir / "media-library-refresh.sh",
            render_template(ctx.package_root, "scripts/media-library-refresh.sh", refresh),
            mode=0o755, user="root", group=ctx.service_group,
            backups=ctx.backups, dry_run=ctx.dry_run, logger=ctx.logger)
        return changed


def _cron_to_calendar(expression: str) -> str:
    """Translate the common cron shapes to a systemd OnCalendar expression.

    Only the forms the schema allows are supported; anything else falls back to
    a safe 15-minute cadence rather than producing a unit systemd will reject.
    """
    parts = expression.split()
    if len(parts) != 5:
        return "*:0/15"
    minute, hour, day, month, weekday = parts
    if (day, month, weekday) != ("*", "*", "*"):
        return "*:0/15"
    if minute.startswith("*/") and hour == "*":
        try:
            step = int(minute[2:])
            if 1 <= step <= 59:
                return f"*:0/{step}"
        except ValueError:
            pass
    if minute.isdigit() and hour == "*":
        return f"*:{int(minute):02d}"
    if minute.isdigit() and hour.isdigit():
        return f"*-*-* {int(hour):02d}:{int(minute):02d}:00"
    if minute.isdigit() and hour.startswith("*/"):
        try:
            step = int(hour[2:])
            if 1 <= step <= 23:
                return f"*-*-* 0/{step}:{int(minute):02d}:00"
        except ValueError:
            pass
    return "*:0/15"


def steps():
    return [InstallSystemdUnits()]
