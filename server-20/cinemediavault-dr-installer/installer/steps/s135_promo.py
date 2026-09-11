"""The optional spoken-preview promo/barker channel.

Genre discovery and the virtual-channel guide themselves need nothing from this
step - they are core application modules installed unconditionally by
``s050_payload``. This step only provisions the *add-on*: a private Python
environment for Kokoro ONNX text-to-speech, the render-reel timer, and the
state directory the render script writes into.

Modelled directly on ``s130_subtitles.InstallWhisper``: heavyweight, installed
disabled unless the operator explicitly turns it on, and the two model files
are never downloaded by this installer - see docs/PROMO-BARKER-CHANNEL.md for
why (they are large, and their upstream distribution point was not something
this package could verify at build time).
"""

from __future__ import annotations

from pathlib import Path

from ..core.fsops import ensure_dir, write_file
from .base import Step, StepResult

#: Pinned to what the live application was built and tested against.
PROMO_REQUIREMENTS = (
    "kokoro-onnx==0.6.1",
    "onnxruntime==1.29.0",
    "soundfile==0.14.0",
    "numpy==2.5.3",
)


class ConfigurePromoChannel(Step):
    id = "promo"
    title = "Promo/barker channel"
    description = "Configure the optional spoken-preview render pipeline."
    depends_on = ("promo_channel.enabled", "promo_channel.model_path",
                  "promo_channel.voices_path", "promo_channel.minutes")
    requires = ("config",)

    def applies(self, ctx) -> bool:
        return bool(ctx.get("promo_channel.enabled"))

    def preview(self, ctx) -> str:
        return ("Provision the promo/barker render pipeline using the operator-"
                "supplied Kokoro model files.")

    def run(self, ctx) -> StepResult:
        directory = ctx.layout.promo_dir
        ensure_dir(directory, mode=0o750, user=ctx.service_user,
                   group=ctx.service_group, dry_run=ctx.dry_run, logger=ctx.logger)
        ensure_dir(directory / "reels", mode=0o750, user=ctx.service_user,
                   group=ctx.service_group, dry_run=ctx.dry_run, logger=ctx.logger)

        warnings: list[str] = []
        for key, label in (("promo_channel.model_path", "kokoro-v1.0.onnx"),
                            ("promo_channel.voices_path", "voices-v1.0.bin")):
            value = (ctx.get(key) or "").strip()
            if value and not ctx.dry_run and not Path(value).is_file():
                warnings.append(
                    f"{label} was not found at {value!r}. The render timer will "
                    f"fail until the file is placed there and "
                    f"`cinevaultctl repair --only promo` is run.")

        settings = {
            "minutes": int(ctx.get("promo_channel.minutes") or 60),
            "schedule_calendar": ctx.get("promo_channel.schedule_calendar"),
        }
        import json
        changed = write_file(
            directory / "settings.json", json.dumps(settings, indent=2) + "\n",
            mode=0o640, user=ctx.service_user, group=ctx.service_group,
            backups=ctx.backups, dry_run=ctx.dry_run, logger=ctx.logger)

        result = StepResult(changed=changed, summary="promo channel settings written",
                            data=settings)
        for message in warnings:
            result.warn(message)
        return result


class InstallPromoRuntime(Step):
    id = "promo.runtime"
    title = "Promo/barker Python environment"
    description = "Create a private virtual environment for Kokoro ONNX text-to-speech."
    depends_on = ("promo_channel.enabled",)
    requires = ("promo", "packages")

    def applies(self, ctx) -> bool:
        return bool(ctx.get("promo_channel.enabled"))

    def preview(self, ctx) -> str:
        return (f"Create a virtual environment and install "
                f"{', '.join(PROMO_REQUIREMENTS)}.")

    def run(self, ctx) -> StepResult:
        venv = ctx.layout.promo_dir / "venv"
        if ctx.dry_run:
            return StepResult(changed=True,
                              summary="would create the promo/barker environment")
        if ctx.offline:
            return StepResult(summary="skipped in offline mode").warn(
                "The promo/barker channel needs to download Python packages; run "
                "`cinevaultctl repair --only promo.runtime` when online.")

        created = False
        if not (venv / "bin" / "python").exists():
            ctx.runner.run(["python3", "-m", "venv", str(venv)], timeout=300)
            created = True

        pip = str(venv / "bin" / "pip")
        ctx.runner.run([pip, "install", "--upgrade", "pip", "wheel"], timeout=600,
                       check=False)
        result = ctx.runner.run([pip, "install", *PROMO_REQUIREMENTS],
                                check=False, timeout=2400)
        if result.returncode != 0:
            return StepResult(changed=created,
                              summary="promo/barker packages did not install").warn(
                "kokoro-onnx could not be installed. The rest of the application "
                "is unaffected. Retry with "
                "`cinevaultctl repair --only promo.runtime`.")

        ctx.runner.run(["chown", "-R",
                        f"{ctx.service_user}:{ctx.service_group}", str(ctx.layout.promo_dir)],
                       check=False)

        return StepResult(changed=True, summary="promo/barker environment ready",
                          data={"venv": str(venv)})


def steps():
    return [ConfigurePromoChannel(), InstallPromoRuntime()]
