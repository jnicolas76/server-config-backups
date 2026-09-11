# CineMediaVault disaster-recovery installer — build report

**Package:** `CineMediaVault-DR-Installer-2026-09-10` v2.2.0 (configuration schema v1)
**Built:** 2026-09-10
**Status:** complete and locally verified; **not yet validated on a clean VM**
(that pass is explicitly reserved for the primary agent/user - see §7).

This package supersedes `CineMediaVault-Complete-Installer` v2.1.0
(2026-09-02/03). It is not a from-scratch rebuild: it is that installer,
reconciled against the live `192.168.1.20` application as it stood on
2026-09-10, after auditing everything shipped since 2.1.0 and finding it was
missing two entire features.

---

## 1. Why this rebuild happened, and what the audit found

The task was explicit: *do not assume the September 1 package is current;
reconcile it against the live September 10 application and every feature
added since then.* The 2.1.0 package's own `BUILD-REPORT.md` was dated
2026-09-02 (with a payload touch-up on 2026-09-03 for themes/usage
analytics). Two full engineering passes happened on the live host after
that and before this one, on 2026-09-08 and 2026-09-09, neither reflected in
the installer at all:

| Date | What shipped live | In the 2.1.0 package? |
|---|---|---|
| 2026-09-08 | Genre discovery (`genre_catalog.py`) | **No - file did not exist in the payload** |
| 2026-09-08 | Ten virtual movie channels + ten virtual TV channels (`virtual_channels.py`, `test_virtual_channels.py`) | **No - file did not exist in the payload** |
| 2026-09-08 | Watch Live crash fix (`channel['number']` → `channel['channel_number']` in `virtual_channels.py`) | N/A - shipped with the file above |
| 2026-09-09 | Promo/barker spoken-preview channel (`generate_combined_barker.py`, `barker_tts_generate.py`, Kokoro ONNX TTS) | **No** |
| (already synced 2026-09-03) | Per-account color themes, usage analytics | Yes - confirmed byte-identical by SHA-256 against the live 2026-09-10 copies of `cinevault_theme.py`, `cinevault_usage.py`, `epg_extend.py`, `music_module.py`, `cinevault_video_lists.py` before touching anything |

### How the audit was performed

1. Read-only `ssh jnicolas@192.168.1.20`: directory listing of
   `~/cinemediavault-lab`, `crontab -l`, `systemctl list-units '*cinevault*'`
   (none - the live host runs CineVault from cron/`@reboot`, not systemd; the
   installer's systemd design is deliberately better and unaffected by this).
2. `sha256sum` on both sides for every shared module before deciding whether
   to re-sync it. Five modules matched exactly and were left untouched, five
   did not, and two live files (`virtual_channels.py`, `genre_catalog.py`)
   had no installer-side counterpart at all.
3. Read the three CineVault engineering reports written between the two
   packages: `CLAUDE_CINEVAULT_GENRE_VIRTUAL_CHANNELS_BRIEF.md`,
   `CLAUDE_CINEVAULT_VIRTUAL_CHANNELS_REPORT.md`,
   `CLAUDE_CINEVAULT_WATCH_LIVE_GUIDE_FIX_REPORT.md` - all under `/mnt/c/Data`.
4. Diffed every candidate file (`diff <(ssh ... cat ...) payload/app/...`)
   rather than trusting file size or mtime, per the standing project note
   that a partially-synced installer payload has burned a prior session
   before (see `docs/USAGE-ANALYTICS.md`'s "known gotcha").
5. Two backup-suffixed files on the live host,
   `tv-metadata-map.json.pre-hunter-fix-20260910012438` and
   `tv_download_server.py.pre-root-encoding-fix-20260906`, were inspected to
   confirm they were **library metadata / a cosmetic mojibake+layout fix**,
   not another undocumented feature - both are already reflected in the
   `tv_download_server.py` copy synced into this package, and the metadata
   file is user data this installer never ships anyway.

**No live system was modified.** Every contact with `192.168.1.20` was a
read-only `ssh`/`scp`. No service was restarted, no file was written, no
database was touched.

---

## 2. What changed in this package

### Payload re-synced from the live 2026-09-10 application

| File | Why |
|---|---|
| `payload/app/cinemediavault.py` | Was missing `import genre_catalog`/`import virtual_channels`, the `user_tv_state` table, the Virtual Channels nav link, and several mobile-UI CSS refinements. Re-synced whole, then the default-administrator patch (`tools/patch-payload.py`) was **re-applied and re-verified** (it is not persistent across a re-sync). |
| `payload/app/dvr_module.py` | One-line diff: added the same Virtual Channels nav link. |
| `payload/app/media-download-library/media_download_server.py` | Added the Genres nav link (2026-09-08 `.bak-vchannels-20260908-160541` on the live host confirms this was the change). |
| `payload/app/tv-download-library/tv_download_server.py` | Genres nav link, plus the already-shipped 2026-09-06 mojibake/layout fix. |
| `payload/app/virtual_channels.py` | **New.** 139 KB. Ten movie + ten TV virtual channels, 14-day persisted guide, includes the Watch Live crash fix. |
| `payload/app/genre_catalog.py` | **New.** Genre tile/poster browsing for Movies and TV. |
| `payload/app/test_virtual_channels.py` | **New.** The application's own test suite for scheduling/seek-offset invariants, shipped alongside the module. |
| `payload/app/barker_tts_generate.py`, `payload/app/generate_combined_barker.py` | **New.** The optional promo/barker channel's render scripts (see below). |
| `payload/scripts/repair_virtual_schedules.py` | **New**, adapted from the live host's operator tool: reads `CINEVAULT_DB`/`CINEVAULT_PORT` from the service environment instead of hand-parsing an env file that does not exist in this installer's layout. |

### A pre-existing bug found and fixed during this reconciliation

`cinemediavault.py`'s `/api` status payload (`homepage_status_payload()`)
hard-coded `"url": "https://192.168.1.20:5000"` - present in the 2.1.0
package too, not something this pass introduced, just never previously
noticed because the payload's per-file diff had never been done against a
live re-sync before. Fixed to build the URL from the requesting hostname
(`handler.request_hostname()`), exactly matching the pattern already used
two lines above it for module URLs (`module_public_url`).

### A trademark/copyright risk found and excluded

`virtual_channels.py` references two static images from
`/assets/`: a generic 25-icon sprite sheet (`channel-logos-sprite-20260910.png`,
original/AI-generated artwork - explosions, masks, planets, a cowboy hat,
etc. - visually inspected before bundling, no third-party trademarks
present) and, for one specific TV channel slot (`T12`, literally named `"The
Simpsons"` in the live application - a thirteenth channel beyond the ten
genre-based movie and ten genre-based TV channels the original virtual-
channels brief specified), `channel-logo-simpsons-clean.png` - which,
visually inspected, **is the actual trademarked "The Simpsons" logo**.

The scheduling logic itself is not a problem: like every other virtual
channel, `T12` only schedules episodes the operator's own library already
contains, matched by title (`simpsons_eligible()`), which is ordinary
personal use of media the operator owns. **The logo image is the actual
risk** - redistributing Fox/Disney's trademarked wordmark inside a
general-purpose installer package. This package ships the sprite sheet but
**excludes `channel-logo-simpsons-clean.png`**. The `T12` channel and its
scheduling behaviour are unaffected; its logo tile simply renders blank
(`background-image` 404) instead of the trademarked wordmark until/unless
an operator supplies their own image at that path - the same posture this
installer already takes toward ROMs, BIOS images and the Android APK.
Recorded in `docs/MODULE-INVENTORY.md` and `docs/THIRD-PARTY-NOTICES.md`.

### A pre-existing stale default assessed as harmless

`epg_extend.py` still defaults `CINEVAULT_EPG_EXTEND_URL` to
`http://192.168.1.134:3010/guide.xml` if the environment variable is unset.
This installer's own `s060_config.py` **always** sets that variable
explicitly from `epg.collector_bind_address`/`epg.collector_port`, so the
hard-coded default is unreachable through this installer. Left as-is (the
file otherwise matched the live host exactly, and editing an unreached
default is not worth the risk of a mismatched re-sync later); flagged here
for whoever next re-syncs this file from upstream.

### New installer engine wiring

Genre discovery and virtual channels needed **no installer changes at all**
beyond the payload sync above - both are core, always-on application modules
with their own SQLite schema created by the application itself (like every
other table in this codebase), and no new environment variable beyond
`CINEVAULT_DB`, which was already wired.

The promo/barker channel is new, optional, and off by default (installed
disabled unless `promo_channel.enabled` is set - the same posture as
Whisper):

- `installer/config/schema.py` — 7 new `promo_channel.*` settings, plus
  cross-field validation requiring the two operator-supplied model file
  paths when enabled.
- `installer/steps/s135_promo.py` — new step, modelled directly on
  `s130_subtitles.InstallWhisper`: creates a private virtual environment,
  installs pinned `kokoro-onnx==0.6.1`, `onnxruntime==1.29.0`,
  `soundfile==0.14.0`, `numpy==2.5.3`, warns (does not fail the install) if
  the configured model files are not yet present.
- `installer/preflight/deps.py` — adds `fonts-dejavu-core` (the reel's
  on-screen text overlay font) when the feature is selected.
- `templates/systemd/cinemediavault-promo.{service,timer}` — new units,
  `ProtectSystem=strict`, capped `CPUQuota`/`MemoryMax`, `Nice=10`/idle I/O,
  wired into `s150_systemd.py` and `s160_timers.py` exactly like every other
  conditional unit in this installer.
- `installer/core/context.py` — one new `Layout.promo_dir` property.

**Known, deliberate gap:** there is no graphical setup-wizard page for the
promo/barker channel yet. The schema, validation, systemd units and Python
environment are fully wired and tested - an unattended
`cinemediavault.yaml` install works end to end - but `wizard/static/wizard.js`
is hand-authored per field (not schema-driven), and adding a wizard stage for
one optional, off-by-default, heavyweight feature was judged lower value
than the reconciliation work itself given the scope of this task. Documented
with the exact continuation pointer in `docs/PROMO-BARKER-CHANNEL.md`.

### Kokoro model files: not bundled, not downloaded

`kokoro-v1.0.onnx` (~325 MB) and `voices-v1.0.bin` (~28 MB) are needed by the
promo/barker channel. Their publisher states an Apache-2.0 licence, but this
build had no way to independently re-verify that grant or record a checksum
against an authoritative distribution point, so - unlike EmulatorJS/js-dos,
whose exact official download endpoints were already established in the
2.1.0 package - no URL for these two files is embedded here. The operator
supplies both files themselves. See `docs/PROMO-BARKER-CHANNEL.md` and
`docs/THIRD-PARTY-NOTICES.md`.

### Documentation added or updated

| Document | Change |
|---|---|
| `docs/VIRTUAL-CHANNELS.md` | **New.** Genre discovery + virtual channels: architecture, scheduling rules, the Watch Live fix, playback, reconciliation notes. |
| `docs/PROMO-BARKER-CHANNEL.md` | **New.** What it is, how to turn it on, the model-file requirement, the wizard-UI gap. |
| `docs/MODULE-INVENTORY.md` | New rows for genre discovery, virtual channels, the promo/barker channel, and the virtual-schedule repair CLI; the ad hoc `rotate_cinevault_refresh_logs.sh` cron job dispositioned as superseded by the existing generated logrotate config; renumbered §14/§15/§16 to make room. |
| `docs/CONFIGURATION-REFERENCE.md`, `config/cinemediavault.schema.json` | Regenerated from the schema (126 settings, up from 119). |
| `docs/THIRD-PARTY-NOTICES.md` | Added kokoro-onnx, onnxruntime, soundfile, numpy (PyPI, MIT/BSD-3), `fonts-dejavu-core` (Ubuntu), and a dedicated section on the un-bundled Kokoro model weights. |
| `README.md` | Two new feature paragraphs, two new doc-table rows. |
| `VERSION`, `installer/version.py` | `2.1.0` → `2.2.0`. |

### Tests added

| Test | What it proves |
|---|---|
| `tests/test_config.py::test_promo_channel_requires_operator_supplied_model_files` | Enabling the feature without both model paths set is a validation error. |
| `tests/test_config.py::test_promo_channel_is_valid_once_model_files_are_set` | ...and is not, once they are. |
| `tests/test_templates.py::test_the_promo_channel_unit_is_resource_limited_and_off_by_default` | The unit is absent from a default configuration, present and resource-capped once enabled, and its `ExecStart` carries the operator's exact model/voice paths. |

The pre-existing generic tests already cover most of the new surface without
modification: `test_every_shipped_template_declares_only_known_variables`
picked up both new systemd templates automatically, and the "every Python
file compiles" static check covers `virtual_channels.py`, `genre_catalog.py`
and `test_virtual_channels.py` as shipped payload.

---

## 3. Testing performed

### Automated — 232 tests, all passing (up from 227 in 2.1.0)

```
$ bash tests/run-tests.sh
CineMediaVault installer test suite
===================================
  OK   every Python file compiles
  OK   wizard JavaScript is lexically sound
...
Ran 232 tests in ~30s
OK
RESULT: PASS
```

### Manual, against the reconciled payload

- Every re-synced and new `.py` file individually compiled
  (`python3 -m py_compile`) before being trusted.
- `tools/patch-payload.py` re-run after the `cinemediavault.py` re-sync;
  confirmed it found and removed the exact upstream default-administrator
  block again (`admin_hash = password_hash("admin1")`,
  `VALUES('jnicolas', ...)`), and that the patched file still compiles.
- Credential/secret scan (the same pattern `tools/make-source-zip.sh` runs
  before packaging - password hashes, private keys, the literal `admin1`
  default) run by hand against every newly-synced and newly-written file;
  clean. Separately grepped for `192.168.1.` across the whole tree; the only
  hits are pre-existing documentation examples, the pre-existing (and, per
  above, unreachable) `epg_extend.py` default, and this report's own
  narration of the audit.
- Config schema round-trip: `installer/config/schema.py` imports cleanly,
  126 fields load, `tools/gen-config-reference.py` and
  `tools/gen-json-schema.py` regenerate without error.

### Not tested, and honestly so (unchanged from 2.1.0, plus one addition)

Everything requiring root or real hardware - package installation, systemd
activation, TLS generation, service startup, tuner discovery, recording, the
Whisper/promo Python environments actually installing packages, and now
specifically **the promo/barker channel actually rendering a reel** (needs
real Kokoro model files and a GPU-optional but non-trivial ffmpeg pass,
neither exercised here). All covered by
`docs/CLEAN-VM-ACCEPTANCE-CHECKLIST.md` and §7 below.

---

## 4. Feature inventory (current, as of this package)

Full detail and disposition for every item: `docs/MODULE-INVENTORY.md`
(16 sections). Summary of what is installed:

Core app · Movies · TV · Genre discovery (Movies/TV) · Ten virtual movie
channels + ten virtual TV channels (14-day guide, deterministic persisted
schedule, Watch Live) · Music · Book Vault · Comics · Games/emulators
(NES/SEGA/DOS/MAME family, downloaded from official sources at install
time) · Live TV/HDHomeRun/grid EPG/extended guide collector/DVR · Playback
(Direct/HLS decision, Video Wall, resume state) · Downloads/Android
companion · Subtitles (existing/SRT, SubDL, optional Whisper) · Promo/barker
channel (optional) · Transcoding · Per-account color themes · Usage
analytics (bandwidth/CPU, Admin dashboard) · Operations (health/self-heal,
refresh, backup, log rotation, modules page).

## 5. Dependency inventory (current, as of this package)

**Always installed (apt):** `python3`, `python3-venv`, `ca-certificates`,
`curl`, `openssl`, `sqlite3`, `ffmpeg`.
**Conditional (apt):** `python3-pil` (Book Vault), `unrar-free`/`p7zip-full`
(Comics), `ghostscript`/`poppler-utils` (creators), `nfs-common`/`cifs-utils`
(network shares), `ufw` (firewall), `docker.io`/`docker-compose-v2`
(extended EPG collector), `python3-dev`/`build-essential` (Whisper),
`fonts-dejavu-core` (promo/barker channel, **new this version**).
**Conditional (PyPI, private venvs):** `faster-whisper==1.0.3`,
`ctranslate2==4.4.0` (subtitles); `kokoro-onnx==0.6.1`,
`onnxruntime==1.29.0`, `soundfile==0.14.0`, `numpy==2.5.3` (promo/barker,
**new this version**).
**Downloaded at install time from official sources (never bundled):**
jsnes (MIT), EmulatorJS (GPL-3.0), js-dos (GPL-2.0), iptv-org/epg (MIT),
`node:22-alpine` (Docker Hub).
**Operator-supplied, never downloaded by this installer:** TMDb/SubDL/Google
Books API keys and tokens; `kokoro-v1.0.onnx` + `voices-v1.0.bin`
(**new this version** - see §2).
Full detail with checksummable sources where feasible:
`docs/THIRD-PARTY-NOTICES.md`.

---

## 6. Remaining risks (in addition to the 2.1.0 list, unchanged and still true)

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **The promo/barker channel has never actually rendered a reel with this package's exact pinned dependency versions.** | medium | the render timer fails on first run | Warned at install time if model files are missing; `cinevaultctl repair --only promo.runtime` retries; failure is isolated to that one timer. |
| **No wizard page for the promo/barker channel.** | certain | it can only be turned on via `cinemediavault.yaml`, not the browser wizard | Documented exactly where to add it (`docs/PROMO-BARKER-CHANNEL.md`); low priority because the feature is off by default and heavyweight. |
| **The ten TV virtual channels' genre list is tallied from one specific library** (`Drama, Comedy, Science Fiction, Action & Adventure, Crime, Mystery, Animation, Family, Documentary, Western`, from the live host on 2026-09-08). | certain | a differently-shaped library gets the same ten channel *slots* but some may have thin or no programming | Same class of risk as the 2.1.0 report's EPG channel-map caveat; documented in `docs/VIRTUAL-CHANNELS.md`. |
| **Kokoro model file provenance could not be independently re-verified at build time.** | certain (by design) | operator must source and validate these files themselves | Deliberate: not shipping an unverified URL is safer than shipping one that turns out wrong. See §2. |
| Everything listed in the 2.1.0 report's own §6 (no root-path code run yet, `ProtectSystem=strict` may need one more path, untested on ARM64/Debian, etc.) | unchanged | unchanged | unchanged; this pass did not touch those areas. |

---

## 7. Exact steps for the clean-VM test

Identical procedure to the 2.1.0 report, plus two additions (marked **NEW**).
Full checklist: `docs/CLEAN-VM-ACCEPTANCE-CHECKLIST.md`.

```bash
# 1. Fresh Ubuntu 22.04 (and separately, 24.04) VM: 4 vCPU, 4 GB RAM, 40 GB disk.
#    Snapshot it. Put a small sample movie/TV library on it.

# 2. Transfer and verify the package
sha256sum -c CineMediaVault-DR-Installer-2.2.0-20260910.zip.sha256
unzip -q CineMediaVault-DR-Installer-2.2.0-20260910.zip
cd CineMediaVault-DR-Installer-2026-09-10

# 3. The suite must pass on the target too
./tests/run-tests.sh

# 4. Dry run changes nothing
cp docs/examples/minimal.yaml /tmp/t.yaml && $EDITOR /tmp/t.yaml
sudo ./install.sh --dry-run --config /tmp/t.yaml
ls /opt/cinemediavault /etc/cinemediavault     # both must not exist

# 5. The wizard
sudo ./install.sh
#    Walk all stages. Confirm resumability (refresh/close/reboot mid-way).

# 6. Verify the reconciled features specifically      # NEW
sudo cinevaultctl smoke-test --deep
#    Must include: "genre discovery API requires authentication" PASS,
#    "virtual-channel schema present" PASS.
curl -sk -o /dev/null -w '%{http_code}\n' https://127.0.0.1:5000/genres/movies
curl -sk -o /dev/null -w '%{http_code}\n' https://127.0.0.1:5000/api/vchannels/status
#    Both should require auth (302/401), not 500/connection-reset - the
#    exact failure mode the 2026-09-08 Watch Live bug produced before its fix.
#    After signing in through the browser: open Virtual Channels, confirm the
#    14-day guide renders, "Watch Live" on a currently-airing program actually
#    starts playback (this is the specific regression this package carries the
#    fix for), and "Play from beginning" works separately.

# 7. Optional: the promo/barker channel                # NEW
#    Only if you have kokoro-v1.0.onnx + voices-v1.0.bin available:
sudo cinevaultctl config set promo_channel.enabled true
sudo cinevaultctl config set promo_channel.model_path /path/to/kokoro-v1.0.onnx
sudo cinevaultctl config set promo_channel.voices_path /path/to/voices-v1.0.bin
sudo cinevaultctl apply
sudo systemctl start cinemediavault-promo.service
sudo journalctl -u cinemediavault-promo.service --no-pager | tail -40
#    Must produce a reel under the promo state directory without error.

# 8. Standard checks (unchanged from 2.1.0): idempotency, backup/restore,
#    reboot survival, self-healing, and the uninstall/media-preservation check.
sudo ./install.sh --config /etc/cinemediavault/cinemediavault.yaml
sudo cinevaultctl backup --label acceptance
sudo cinevaultctl restore /var/lib/cinemediavault/backups/*acceptance.tar.gz
ls -la /srv/media/Movies > /tmp/before.txt
sudo cinevaultctl uninstall --remove-state --remove-generated-media --yes
ls -la /srv/media/Movies > /tmp/after.txt
diff /tmp/before.txt /tmp/after.txt      # must be identical

# 9. Repeat steps 5-8 on Ubuntu 24.04, unattended:
sudo ./install.sh --config /tmp/full.yaml
```

Record any failure with the command, its output, and
`/var/log/cinemediavault/install-*.jsonl`.

### What would make the pass a failure

Everything in the 2.1.0 report's list, plus:

- `/watch/vchannel/<id>` failing (connection reset, not a clean HTTP
  response) on a currently-airing program - the exact 2026-09-08 regression;
- the genre or virtual-channel API/pages being reachable **without**
  authentication;
- the promo/barker timer running when `promo_channel.enabled` is `false`
  (it must not exist at all in that case, not merely be stopped).

---

## 8. Deliberately not done

Per the task, and left for the primary agent/user:

- **Nothing was published.** No GitHub push, no Nextcloud copy.
- **No live system was modified, restarted, or written to.** Every contact
  with `192.168.1.20` was a read-only `ssh`/`scp` (§1).
- **The clean-VM acceptance pass was not run.** This report documents the
  exact steps (§7); a clean-VM/clean-container run and its result are not
  claimed here.
- **The Android APK is not bundled** (unverifiable provenance, unchanged
  reasoning from 2.1.0).
- **Kokoro model files are not bundled or downloaded** (§2).
- **No ROMs, BIOS images, media or content of any kind.**
- **The promo/barker wizard-UI gap is not closed** (§2) - documented with an
  exact continuation pointer instead.

---

## 9. Final archive

| | |
|---|---|
| Package directory | `/mnt/c/Data/CineMediaVault-DR-Installer-2026-09-10/` |
| Archive | `/mnt/c/Data/CineMediaVault-DR-Installer-2.2.0-20260910.zip` |
| SHA-256 | see `/mnt/c/Data/CineMediaVault-DR-Installer-2.2.0-20260910.zip.sha256`, generated by `tools/make-source-zip.sh` at build time (recorded verbatim, not retyped, to rule out transcription error) |

`tools/make-source-zip.sh` refuses to build if it finds a credential-shaped
string anywhere in the tree; it succeeded on this build.
