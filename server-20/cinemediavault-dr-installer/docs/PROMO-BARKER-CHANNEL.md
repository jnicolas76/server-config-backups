# Promo/barker channel (optional)

Added to the live application on 2026-09-09. Off by default in this package
for the same reason Whisper is off by default: it is heavyweight (a private
~350 MB Python environment, plus operator-supplied model files) and nothing
breaks by leaving it off.

## What it does

`generate_combined_barker.py` renders a one-hour, spoken-narration preview
reel of upcoming virtual-channel programming: short clips from real local
movies/episodes, a synchronized on-screen info panel, and Kokoro
text-to-speech narration ("Coming up at 6:00 PM on Action, ...") mixed under
a ducked audio bed. `barker_tts_generate.py` is a smaller, standalone Kokoro
helper `virtual_channels.py` calls directly (via the
`CINEVAULT_BARKER_TTS_PYTHON` / `_SCRIPT` / `_MODEL` / `_VOICES` environment
this installer writes when the feature is enabled) for shorter, on-demand
announcements.

## Turning it on

1. Obtain `kokoro-v1.0.onnx` (~325 MB) and `voices-v1.0.bin` (~28 MB) - the
   Kokoro-82M text-to-speech weights and voice pack - yourself. **This
   installer does not fetch, bundle or verify these files.** See
   `docs/THIRD-PARTY-NOTICES.md` for exactly why: their publisher states an
   Apache-2.0 licence, but this package's build had no way to independently
   re-verify that grant or record a checksum against an authoritative source,
   so no download URL is embedded here either. Confirm the licence and
   integrity of whatever copy you obtain before using it.
2. Place both files somewhere the installer can read at install time.
3. Set, in `cinemediavault.yaml` (or the equivalent wizard-saved
   configuration):
   ```yaml
   promo_channel:
     enabled: true
     model_path: /path/to/kokoro-v1.0.onnx
     voices_path: /path/to/voices-v1.0.bin
   ```
   Optional: `schedule_calendar` (systemd `OnCalendar` syntax, default four
   times a day - `*-*-* 05,11,17,23:05:00`, one hour before each six-hour
   broadcast boundary), `minutes` (reel length, default 60),
   `cpu_quota_percent` / `memory_limit_mb` (systemd ceilings for the render
   timer, defaults 150% / 4096 MB).
4. Install or re-run `cinevaultctl apply`. `installer/steps/s135_promo.py`
   creates a private virtual environment (`kokoro-onnx==0.6.1`,
   `onnxruntime==1.29.0`, `soundfile==0.14.0`, `numpy==2.5.3`, pinned to what
   the live application was built and tested against) and installs
   `fonts-dejavu-core` (the on-screen text overlay's font). If the model
   files are not found where configured, the step warns rather than failing
   the whole install, and names the exact repair command.

## Known gap in this package

**There is no graphical setup-wizard page for this feature yet.** The
schema fields (`promo_channel.*` in `installer/config/schema.py`), the
systemd unit/timer (`templates/systemd/cinemediavault-promo.{service,timer}`),
and the Python-environment step are all fully wired and tested
(`tests/test_config.py::PromoChannel*`,
`tests/test_templates.py::test_the_promo_channel_unit_is_resource_limited_and_off_by_default`) -
an unattended `cinemediavault.yaml` install works end to end. Only the
interactive wizard (`wizard/static/wizard.js`, hand-authored per field, not
schema-driven) does not yet render a form for it. Continuing this: add a
`renderPromoStage()` following the existing `subtitles`/Whisper stage as a
template, and add its fields to the `modules` stage's rendered form (the
schema/validation ownership is already in place in `wizard/api.py`'s
`STAGE_FIELDS["modules"]`).

## Operational notes

- The render timer runs as the unprivileged service user, `ProtectSystem=strict`,
  a capped `CPUQuota`/`MemoryMax`, and `Nice=10`/idle I/O so a render never
  competes with playback.
- A failed render (missing model files, a bad source file) fails that one
  timer run; it does not affect virtual channels, genre discovery, or
  anything else. Retry with `cinevaultctl repair --only promo.runtime` after
  fixing the underlying problem.
- Disabling the feature later (`promo_channel.enabled: false` +
  `cinevaultctl apply`) disables and stops the timer; the venv and any
  rendered reels are left in place unless you also uninstall.
