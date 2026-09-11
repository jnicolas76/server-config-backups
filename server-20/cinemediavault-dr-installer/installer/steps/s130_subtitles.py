"""Subtitle discovery, SubDL lookup and the optional Whisper pipeline.

Discovery (sidecar ``.srt`` files and embedded text streams) is part of the
application and needs no installation. This step provisions the two *optional*
generators:

* **SubDL** - one exact title/year lookup per asset per day, capped at the
  documented free-tier limit, with the API key kept in the root-only secret file.
* **Whisper** - local transcription in a private virtual environment. It is
  genuinely heavyweight: a multi-gigabyte model download and, on CPU, hours per
  title. It is therefore installed disabled unless the operator explicitly asked
  for it to start, and always under a systemd CPU and memory ceiling.

Nothing here ever overwrites an existing subtitle file.
"""

from __future__ import annotations

from ..core.fsops import ensure_dir, write_file
from .base import Step, StepResult

#: Pinned so a repeat install gets the same behaviour, and so a surprise
#: upstream release cannot change transcription quality under the operator.
WHISPER_REQUIREMENTS = (
    "faster-whisper==1.0.3",
    "ctranslate2==4.4.0",
)

#: Approximate on-disk size of each model, used for the preview and the
#: free-space check.
MODEL_SIZE_MB = {
    "tiny": 75, "base": 145, "small": 480, "medium": 1500, "large-v3": 3100,
}


class ConfigureSubtitles(Step):
    id = "subtitles"
    title = "Subtitles"
    description = "Configure subtitle discovery and the optional generators."
    depends_on = ("subtitles.use_existing", "subtitles.languages",
                  "subtitles.subdl_enabled", "subtitles.subdl_daily_search_limit")
    requires = ("config",)

    def preview(self, ctx) -> str:
        languages = ", ".join(ctx.get("subtitles.languages") or ["en"])
        parts = [f"Discover existing subtitles in {languages}"]
        if ctx.get("subtitles.subdl_enabled"):
            parts.append(f"look up missing ones on SubDL (max "
                         f"{ctx.get('subtitles.subdl_daily_search_limit')} searches/day)")
        return "; ".join(parts) + "."

    def run(self, ctx) -> StepResult:
        directory = ctx.layout.state_root / "subtitles"
        ensure_dir(directory, mode=0o750, user=ctx.service_user,
                   group=ctx.service_group, dry_run=ctx.dry_run, logger=ctx.logger)

        settings = {
            "languages": list(ctx.get("subtitles.languages") or ["en"]),
            "use_existing": bool(ctx.get("subtitles.use_existing")),
            "subdl_enabled": bool(ctx.get("subtitles.subdl_enabled")),
            "subdl_daily_search_limit":
                int(ctx.get("subtitles.subdl_daily_search_limit") or 2000),
            "whisper_enabled": bool(ctx.get("subtitles.whisper_enabled")),
            "whisper_model": ctx.get("subtitles.whisper_model"),
            "whisper_device": ctx.get("subtitles.whisper_device"),
            "whisper_workers": int(ctx.get("subtitles.whisper_workers") or 1),
            # Never overwrite what the operator already has. This is the single
            # most important subtitle setting and it is not configurable.
            "never_overwrite_existing": True,
        }
        import json
        changed = write_file(
            directory / "settings.json", json.dumps(settings, indent=2) + "\n",
            mode=0o640, user=ctx.service_user, group=ctx.service_group,
            backups=ctx.backups, dry_run=ctx.dry_run, logger=ctx.logger)

        result = StepResult(changed=changed, summary="subtitle settings written",
                            data=settings)
        if settings["subdl_enabled"]:
            result.warn(
                "SubDL free-tier limits apply (searches and downloads are capped "
                "per day). The pipeline stops for the day when the budget is "
                "spent and resumes automatically the next day.")
        return result


class InstallWhisper(Step):
    id = "subtitles.whisper"
    title = "Whisper transcription"
    description = ("Create a private Python environment for local subtitle "
                   "transcription and install the model on first use.")
    depends_on = ("subtitles.whisper_enabled", "subtitles.whisper_model",
                  "subtitles.whisper_device", "subtitles.whisper_workers")
    requires = ("subtitles", "packages")

    def applies(self, ctx) -> bool:
        return bool(ctx.get("subtitles.whisper_enabled"))

    def preview(self, ctx) -> str:
        model = ctx.get("subtitles.whisper_model")
        size = MODEL_SIZE_MB.get(model, 500)
        return (f"Create a virtual environment and install faster-whisper. The "
                f"'{model}' model (~{size} MB) downloads on first use. "
                f"CPU limited to {ctx.get('subtitles.whisper_cpu_quota_percent')}%, "
                f"memory to {ctx.get('subtitles.whisper_memory_limit_mb')} MB.")

    def run(self, ctx) -> StepResult:
        directory = ctx.layout.state_root / "subtitles"
        venv = directory / "venv"
        warnings: list[str] = []

        ensure_dir(venv.parent, mode=0o750, user=ctx.service_user,
                   group=ctx.service_group, dry_run=ctx.dry_run, logger=ctx.logger)
        ensure_dir(ctx.layout.cache_root / "whisper", mode=0o750,
                   user=ctx.service_user, group=ctx.service_group,
                   dry_run=ctx.dry_run, logger=ctx.logger)

        # Free space for the model, checked before spending minutes on a venv.
        model = ctx.get("subtitles.whisper_model")
        needed_mb = MODEL_SIZE_MB.get(model, 500) * 2  # download + extracted
        from ..core.fsops import free_bytes, human_bytes
        try:
            available = free_bytes(ctx.layout.cache_root)
            if available < needed_mb * 1024 * 1024:
                return StepResult(
                    summary="skipped: not enough space for the model"
                ).warn(
                    f"Whisper needs about {needed_mb} MB for the '{model}' model "
                    f"but only {human_bytes(available)} is free on "
                    f"{ctx.layout.cache_root}. Whisper was not installed; free "
                    f"space and run `cinevaultctl repair --only subtitles.whisper`.")
        except OSError:
            pass

        if ctx.dry_run:
            return StepResult(changed=True,
                              summary="would create the Whisper environment")
        if ctx.offline:
            return StepResult(summary="skipped in offline mode").warn(
                "Whisper needs to download packages and a model; run "
                "`cinevaultctl repair --only subtitles.whisper` when online.")

        created = False
        if not (venv / "bin" / "python").exists():
            ctx.runner.run(["python3", "-m", "venv", str(venv)], timeout=300)
            created = True

        pip = str(venv / "bin" / "pip")
        ctx.runner.run([pip, "install", "--upgrade", "pip", "wheel"], timeout=600,
                       check=False)
        result = ctx.runner.run([pip, "install", *WHISPER_REQUIREMENTS],
                                check=False, timeout=2400)
        if result.returncode != 0:
            return StepResult(changed=created,
                              summary="Whisper packages did not install").warn(
                "faster-whisper could not be installed. Subtitle discovery and "
                "SubDL still work. Retry with "
                "`cinevaultctl repair --only subtitles.whisper`.")

        # Ownership: the service user must own its own environment.
        ctx.runner.run(["chown", "-R",
                        f"{ctx.service_user}:{ctx.service_group}", str(directory)],
                       check=False)

        outcome = StepResult(
            changed=True,
            summary=f"Whisper environment ready (model '{model}' downloads on "
                    f"first use)",
            data={"venv": str(venv), "model": model},
        )
        if ctx.get("subtitles.whisper_device") in ("auto", "cpu") and \
                "nvidia" not in ctx.facts.gpu_vendors:
            outcome.warn(
                "No NVIDIA GPU was detected, so transcription runs on the CPU. "
                "Expect roughly 2-6x real time with the 'small' model; a full "
                "catalogue can take days. The worker is niced and CPU-capped so "
                "it does not disturb playback.")
        for message in warnings:
            outcome.warn(message)
        return outcome


def steps():
    return [ConfigureSubtitles(), InstallWhisper()]
