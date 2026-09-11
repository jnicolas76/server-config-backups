# Third-party notices

CineMediaVault's own code is the property of its author. This document records
every third-party component, its licence, and how it is obtained.

**Nothing under a copyleft licence is redistributed inside this package.**
Copyleft components are downloaded from their official sources during
installation, so their provenance stays verifiable and their licences are not
strained by redistribution.

---

## Bundled in this package

| Component | Licence | Where | Why bundling is appropriate |
|---|---|---|---|
| hls.js | Apache-2.0 | `payload/assets/hls.min.js` | Apache-2.0 explicitly permits redistribution with attribution. The notice ships beside it as `hls.min.js.LICENSE.txt`. It is needed for playback on the first page load, before any download could happen. |

Everything else in `payload/` is CineMediaVault's own source.

---

## Downloaded during installation

| Component | Licence | Source | Needed by |
|---|---|---|---|
| jsnes | MIT | `cdn.jsdelivr.net/npm/jsnes` | the NES module |
| EmulatorJS | **GPL-3.0** | `cdn.emulatorjs.org` | SEGA, arcade, Game Boy, GBA, N64, PS1, C64, Atari modules |
| js-dos | **GPL-2.0** | `js-dos.com` | the DOS module |
| iptv-org/epg | MIT | `github.com/iptv-org/epg` | the extended guide collector, cloned at image build time |
| faster-whisper | MIT | PyPI | local subtitle transcription |
| CTranslate2 | MIT | PyPI | the inference runtime under faster-whisper |
| kokoro-onnx | MIT | PyPI | text-to-speech runtime for the optional promo/barker channel |
| onnxruntime | MIT | PyPI | the ONNX inference engine kokoro-onnx runs on |
| soundfile | BSD-3-Clause | PyPI | WAV encode/decode for the promo/barker render pipeline |
| numpy | BSD-3-Clause | PyPI | array support required by kokoro-onnx/onnxruntime |
| node:22-alpine | see the image | Docker Hub | the base image for the guide collector |

Downloads happen only for components you selected, and only after you have
accepted the licences on the first wizard step (`deployment.accept_licenses`).
`--offline` skips all of them; the affected modules are then installed but
inert, and the installer tells you so.

### About the GPL components

EmulatorJS and js-dos are excellent, and their licences are not an obstacle to
*using* them - only to casually redistributing them inside a package that is
otherwise not GPL. Downloading them from upstream at install time keeps the
distinction clean and means you always get the version their maintainers are
publishing.

If you need a fully offline install, fetch these yourself and place them under
`/opt/cinemediavault/modules/`, honouring their licence terms.

---

## Installed from the Ubuntu archive

These come from Ubuntu with their own packaging and licences; this installer
only requests them.

| Package | Licence | Needed by |
|---|---|---|
| `python3` | PSF | everything |
| `ffmpeg` | LGPL-2.1+ / GPL-2+ depending on build | playback conversion, recording, thumbnails, audio probing |
| `sqlite3` | public domain | the databases |
| `openssl` | Apache-2.0 | certificate generation |
| `curl`, `ca-certificates` | MIT / MPL-2.0 | downloads and health probes |
| `docker.io`, `docker-compose-v2` | Apache-2.0 | the guide collector |
| `python3-pil` (Pillow) | HPND | Book Vault cover rendering |
| `unrar-free` | GPL-2+ | CBR comic archives |
| `p7zip-full` | LGPL-2.1+ | 7z and zip archives |
| `ghostscript` | AGPL-3.0 | PDF handling in the comic generators |
| `poppler-utils` | GPL-2+ | PDF handling in the book generators |
| `nfs-common`, `cifs-utils` | GPL-2+ | network shares |
| `ufw` | GPL-3 | firewall rules |
| `logrotate` | GPL-2+ | log rotation |
| `python3-venv` | PSF | the private environment for local transcription and for the promo/barker channel |
| `python3-dev`, `build-essential` | GPL-3+ / various | compiling any transcription dependency that has no prebuilt wheel for this platform |
| `fonts-dejavu-core` | Bitstream Vera-derived, free | the on-screen text overlay in promo/barker channel reels |

### Kokoro model weights (promo/barker channel only)

The optional promo/barker channel (`promo_channel.enabled`) needs two binary
model files - `kokoro-v1.0.onnx` (~325 MB) and `voices-v1.0.bin` (~28 MB), the
Kokoro-82M text-to-speech weights and voice pack. **This installer does not
download, bundle, or redistribute them.** Their publisher states the weights
are Apache-2.0 licensed, but this package's build could not independently
re-verify that licence grant or fetch a checksummed copy from an authoritative
source at build time, so no download URL is embedded here either. The
operator supplies both files themselves (`promo_channel.model_path`,
`promo_channel.voices_path`) and is responsible for confirming the licence
terms of whatever copy they obtain. See `docs/PROMO-BARKER-CHANNEL.md`.

### The non-free `unrar`

Some RAR5 comic archives need the original non-free `unrar`, whose licence
forbids redistribution and derivative use. This installer will not install it for
you. If you need it:

```bash
sudo add-apt-repository multiverse
sudo apt-get install unrar
```

That is your decision to make under its terms, not one an installer should make
on your behalf.

---

## Services this software talks to

| Service | What for | Terms |
|---|---|---|
| The Movie Database (TMDb) | posters, summaries, cast, genres | your own API key, subject to TMDb's terms. This product uses the TMDb API but is not endorsed or certified by TMDb. |
| SubDL | subtitle lookup | your own API key; free-tier daily limits apply and are enforced |
| Google Books | book metadata | your own optional API key |
| TVPassport, via iptv-org/epg | extended guide data | the upstream project's terms; request pacing defaults to one request every 2.5 seconds |
| SiliconDust discovery | finding tuners on your network | optional; manual entry always works |

No telemetry of any kind is collected or transmitted by CineMediaVault or by this
installer.

---

## Content

**No media, ROMs, BIOS images, comics, books, music or games are included in this
package, and none is ever downloaded.** You supply content you are entitled to
possess and use.

The emulator modules are software that can run game files. Obtaining those files
lawfully is your responsibility.

**One trademarked logo image was found during the 2026-09-10 reconciliation
and deliberately excluded**: the live application's `T12` virtual TV channel
(a single-show slot, "The Simpsons") used a static image that is the actual
Fox/Disney trademarked wordmark, not original artwork. It is not in this
package. The channel's scheduling behaviour - organizing episodes you already
own - is unaffected; only its logo tile is blank until you supply your own
image. The generic per-genre icon sprite sheet used by the other channels
(`channel-logos-sprite-20260910.png`) was visually inspected and contains no
third-party trademarks, so it is bundled normally.

---

## Reproducing this list

The authoritative list lives in the code:

- bundled assets: `payload/assets/`
- install-time downloads: `RUNTIME_SOURCES` in `installer/steps/s100_modules.py`,
  `WHISPER_REQUIREMENTS` in `installer/steps/s130_subtitles.py`, and
  `PROMO_REQUIREMENTS` in `installer/steps/s135_promo.py`
- Ubuntu packages: `BASE_PACKAGES` and `FEATURE_PACKAGES` in
  `installer/preflight/deps.py`
