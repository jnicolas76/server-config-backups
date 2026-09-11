"""Transcoding configuration.

Two very different things share the word "transcode" here:

* **On-demand HLS** - what the player uses when a client cannot direct-play a
  file. It writes only into the cache directory and never touches source media.
  Safe, and on by default.
* **The bulk library queue** - the offline pipeline that re-encodes the library
  to save space. It *rewrites source media*. It is therefore installed stopped,
  with an empty queue, and is never started by the installer regardless of
  configuration. Arming it is a deliberate, separate act.
"""

from __future__ import annotations

import json

from ..core.fsops import ensure_dir, write_file
from ..preflight.gpu import recommended_encoder
from .base import Step, StepResult


class ConfigureTranscoding(Step):
    id = "transcode"
    title = "Transcoding"
    description = "Configure on-demand HLS and, if selected, the bulk library queue."
    depends_on = ("transcode.hls_enabled", "transcode.hls_encoder",
                  "transcode.hls_video_bitrate", "transcode.library_queue_enabled",
                  "transcode.default_playback_mode")
    requires = ("config",)

    def preview(self, ctx) -> str:
        if not ctx.get("transcode.hls_enabled"):
            return "On-demand HLS disabled; clients must direct-play."
        encoder = ctx.get("transcode.hls_encoder")
        resolved = recommended_encoder(ctx) if encoder == "auto" else encoder
        parts = [f"On-demand HLS using {resolved} at "
                 f"{ctx.get('transcode.hls_video_bitrate')}"]
        if ctx.get("transcode.library_queue_enabled"):
            parts.append("bulk library queue installed but STOPPED with an empty queue")
        return "; ".join(parts) + "."

    def run(self, ctx) -> StepResult:
        ensure_dir(ctx.layout.hls_cache_dir, mode=0o750, user=ctx.service_user,
                   group=ctx.service_group, dry_run=ctx.dry_run, logger=ctx.logger)

        encoder = ctx.get("transcode.hls_encoder")
        resolved = recommended_encoder(ctx) if encoder == "auto" else encoder

        # Persist the playback mode file the application reads at startup.
        changed = write_file(
            ctx.layout.state_root / "playback-mode.txt",
            (ctx.get("transcode.default_playback_mode") or "direct") + "\n",
            mode=0o640, user=ctx.service_user, group=ctx.service_group,
            backups=ctx.backups, dry_run=ctx.dry_run, logger=ctx.logger)

        result = StepResult(
            changed=changed,
            summary=f"HLS {'enabled' if ctx.get('transcode.hls_enabled') else 'disabled'}"
                    f", encoder {resolved}",
            data={"encoder": resolved},
        )

        if not ctx.get("transcode.library_queue_enabled"):
            return result

        changed |= self._install_queue(ctx, result)
        result.changed = changed
        return result

    def _install_queue(self, ctx, result: StepResult) -> bool:
        directory = ctx.layout.state_root / "transcode"
        ensure_dir(directory, mode=0o750, user=ctx.service_user,
                   group=ctx.service_group, dry_run=ctx.dry_run, logger=ctx.logger)

        settings = {
            "enabled": True,
            # The installer never arms this. Starting it is a separate,
            # deliberate command precisely because it rewrites source media.
            "autostart": False,
            "state": "stopped",
            "queue": [],
            "workers": 1,
            "container": "mp4",
            "video_codec": "libx265",
            "preset": "slow",
            "audio_kbps": 160,
            "target_gb_per_hour": 0.5,
            "retries": 3,
            "retry_delay_seconds": 300,
            "exclude_patterns": ["sample", "trailer", "extras"],
            # Safety rails that are not configurable.
            "verify_before_replace": True,
            "never_delete_original_on_exit_zero_alone": True,
            "quarantine_repeated_failures": True,
        }
        changed = write_file(
            directory / "queue-settings.json", json.dumps(settings, indent=2) + "\n",
            mode=0o640, user=ctx.service_user, group=ctx.service_group,
            backups=ctx.backups, dry_run=ctx.dry_run, logger=ctx.logger)

        result.warn(
            "The bulk library transcode queue was installed STOPPED with an "
            "empty queue and will not start on its own. It rewrites source "
            "media, so it must be armed deliberately with "
            "`cinevaultctl transcode enable` after you have reviewed its "
            "settings and confirmed you have backups.")
        if ctx.get("transcode.library_queue_autostart"):
            result.warn(
                "transcode.library_queue_autostart was set, but the installer "
                "refuses to start a queue that rewrites source media. Start it "
                "manually once you are satisfied with a test batch.")
        return changed


def steps():
    return [ConfigureTranscoding()]
