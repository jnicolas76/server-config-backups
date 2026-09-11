# CineMediaVault component and module inventory

Every CineVault-related component found in the project handoff, the
`all-software` archive, the `server-config-backups` repository and the live
`.20` host is listed here with an explicit disposition:

| Disposition | Meaning |
|---|---|
| **Installed** | Installed and enabled by a standard installation. |
| **Optional** | Shipped and installable, but off unless selected. |
| **Superseded** | Replaced by something newer; not installed. |
| **Excluded** | Deliberately not installed, with the reason stated. |
| **External** | Someone else's software this installer only links to or reads. |

The sources surveyed were:

- `CINEMEDIAVAULT_CONVERSATION_HANDOFF.md` and its dated updates
- `HOMELAB_CINEVAULT_TRANSCODE_DOCKER_REFERENCE_2026-08-30.md`
- `CineVault-DVR/` and `CINEVAULT_EPG_EXTENSION_2026-09-01.md`
- `CineVault-EPG-Extension/`
- `all-software/` (`.20-cinevault`, `.134-docker`, `.232-transcode`, `wsl`)
- `server-config-backups/server-20/cinemediavault-lab/`
- read-only inspection of `192.168.1.20` for the Book Vault source only
- **2026-09-10 reconciliation**: read-only re-inspection of `192.168.1.20`
  against the 2026-09-02 package (`CineMediaVault-Complete-Installer`), plus
  `CLAUDE_CINEVAULT_VIRTUAL_CHANNELS_REPORT.md`,
  `CLAUDE_CINEVAULT_WATCH_LIVE_GUIDE_FIX_REPORT.md`, and the earlier
  `CLAUDE_CINEVAULT_USER_THEMES_RESULT_2026-09-03.md` /
  `CLAUDE_CINEVAULT_USAGE_ANALYTICS_RESULT_2026-09-03.md` (already reflected
  in the 2026-09-02 package's later 2.1.0 revision, and reconfirmed by
  SHA-256 comparison to still match the 2026-09-10 live files unchanged)

---

## 1. Core application

| Component | Source | Disposition | Notes |
|---|---|---|---|
| CineMediaVault web application | `cinemediavault-lab-5000.py` | **Installed** | Installed as `app/cinemediavault.py`. Authentication, home, search, history, playback, admin. Pure standard library - no third-party Python is needed at runtime. |
| Video list mixin | `cinevault_video_lists.py` | **Installed** | Play queue and playlists. Imported by the main application; taken from `all-software/.20-cinevault` because it is absent from the backup repository. |
| Genre discovery | `genre_catalog.py` | **Installed** | Added to the live application 2026-09-08; synced into this payload 2026-09-10 (it was missing from the 2026-09-02 package). Genre tile grids and poster grids for Movies and TV, read live from the already-loaded catalogue metadata - no database, no TMDb calls per browse. See `docs/VIRTUAL-CHANNELS.md`. |
| Virtual channels (10 movie + 10 TV, plus one single-show TV slot `T12`) | `virtual_channels.py`, `test_virtual_channels.py` | **Installed** | Added to the live application 2026-09-08, Watch Live crash fixed 2026-09-08; synced into this payload 2026-09-10. Clock-driven 14-day cable-style guide generated from the local library; its own SQLite tables, its own background scheduler thread started from `main()`. No installer switch - always on, like Movies or TV. See `docs/VIRTUAL-CHANNELS.md`. |
| Channel logo sprite sheet | `payload/assets/channel-logos-sprite-20260910.png` | **Installed** | Generic per-genre icon artwork, visually inspected 2026-09-10 - no third-party trademarks. |
| `T12` ("The Simpsons") channel logo | `channel-logo-simpsons-clean.png` (live host only) | **Excluded** | Visually inspected 2026-09-10: this is Fox/Disney's actual trademarked "The Simpsons" wordmark, not original artwork. Not bundled. The `T12` channel itself (scheduling the operator's own owned episodes by title match) is unaffected and still installed; only its logo tile renders blank until an operator supplies their own image at that asset path - the same posture as ROMs, BIOS images and the Android APK below. |
| Default administrator bootstrap | inside the main application | **Excluded, and actively removed** | Upstream upserts a hard-coded super-administrator with a publicly known password on every start. The installer **patches this out** and substitutes the account you create in the wizard. The install fails rather than proceeding if the patch cannot be applied. See `installer/steps/s050_payload.py`. |
| Legacy port 8093 instance | live `.20` | **Superseded** | Port 5000 was promoted to canonical on 2026-08-30. New installs get one instance. |
| Top-level `media_download_server.py` | `cinemediavault-lab/` | **Superseded** | A compatibility copy that the wrapper does *not* load. Only `media-download-library/media_download_server.py` is installed. |

## 2. Movies

| Component | Source | Disposition | Notes |
|---|---|---|---|
| Movie module | `media-download-library/media_download_server.py` | **Installed** | Scanning, catalogue, detail pages, Fix Match. |
| TMDb metadata fetcher | `fetch_tmdb_movie_metadata.py` | **Installed** | Run by `cinemediavault-metadata.timer`, not during installation by default. |
| TMDb poster fetcher | `fetch_tmdb_posters.py` | **Installed** | As above. |
| Durable manual match overrides | `manual-metadata-overrides.json`, `manual-poster-overrides.json` | **Installed** (mechanism) | The override *files* are user data and are not shipped. The installer points the application at `state/metadata/movies/` so overrides survive refreshes, which is the fix recorded on 2026-08-31. |
| Stable `asset:` identity migration | in the movie module | **Installed** | Carried over unchanged; migrates legacy exact-path overrides at startup. |
| Movie CSV / HTML report tools | `movie_csv_to_html.py`, `export_movie_file_sizes_csv.py` | **Excluded** | One-off reporting scripts tied to the original host's paths. Not part of a server install. |

## 3. TV

| Component | Source | Disposition | Notes |
|---|---|---|---|
| TV module | `tv-download-library/tv_download_server.py` | **Installed** | Shows, seasons, episodes. |
| TMDb TV metadata / posters | `fetch_tmdb_tv_metadata.py`, `fetch_tmdb_tv_posters.py` | **Installed** | Scheduled, not run at install time. |
| Episode thumbnails | `generate_tv_episode_thumbnails.py` | **Installed** | `cinemediavault-thumbnails.timer`, CPU-capped and at lowest I/O priority. |
| Episode naming / audit scripts | `normalize_tv_episode_files.py`, `audit_tv_episode_sizes.py`, … | **Excluded** | Library-maintenance one-offs that rename source media. Deliberately not installed: this installer never modifies source media. |

## 4. Music

| Component | Source | Disposition | Notes |
|---|---|---|---|
| Music module | `music_module.py` | **Optional** (`modules.music`) | Incremental ffprobe scan, artists/albums/tracks/genres, byte-range streaming, playlists, ZIP downloads. |
| Persistent playback state | `music_playback_state` table | **Installed with the module** | Per-user resume; restored state never autoplays. |
| Automatic startup scan | `CINEMEDIAVAULT_MUSIC_AUTO_SCAN` | **Excluded by default** | Set to `0`. A large music library takes far longer to walk than movies and TV; use **Scan Music** or the scheduled refresh. |
| Music organisation tools | `apply_music_organization.py`, `convert_music_to_plex_layout.py`, `remove_exact_music_duplicates.py` | **Excluded** | They move, rewrite and delete source audio files. |

## 5. Book Vault

| Component | Source | Disposition | Notes |
|---|---|---|---|
| Book Vault server | `bookvault_server.py` (read-only copy from `.20`) | **Optional** (`modules.bookvault`) | EPUB and PDF library with covers and metadata. Runs as its own hardened systemd service. |
| Cover cache builder | `build_bookvault_cache.py` | **Optional** | Installed alongside the server. |
| Pillow dependency | `python3-pil` | **Installed with the module** | The only module needing a third-party Python package; installed from Ubuntu's archive, not pip. |
| Google Books metadata | `GOOGLE_BOOKS_API_KEY` | **Optional** | Key stored in `secrets.env`. |
| Audiobooks | - | **Installed as files** | Audio under the books root is served and downloadable. There is no separate chapterised audiobook player in the current source; the Music module is the better path for long-form audio and has resume support. |
| Kindle transfer script | `transfer_books_to_kindle.ps1` | **Excluded** | Windows PowerShell, workstation-side. |

## 6. Comics

| Component | Source | Disposition | Notes |
|---|---|---|---|
| Comics library server | static file server | **Optional** (`modules.comics`) | Serves the generated comic library over `cinemediavault-module@comics.service`. |
| Comic collection generator | `build_new_comic_collections.py` | **Optional** (`modules.creators` = `comics`) | Unpacks CBZ/CBR into per-issue web galleries. Paths parameterised by the installer; the originals had them hard-coded. |
| Recent-collection generator | `build_recent_comic_collections.py` | **Optional** | Incremental variant. |
| Collection hub builder | `build_collection_hub.py` | **Optional** | Builds the index across collections. |
| Magazine importer | `import_mad_magazine_cbrs.py` | **Optional** | Imports a scanned magazine run. |
| Magazine promoter | `promote_mad_magazine_collection.py` | **Optional** | Promotes an imported run into a collection. |
| RAR extraction | `unrar-free`, `p7zip-full` | **Installed with the module** | The non-free `unrar` handles a few RAR5 variants that `unrar-free` does not. Its licence forbids redistribution, so the installer names it in a warning but never installs it for you. |

## 7. Games and emulators

| Component | Source | Disposition | Notes |
|---|---|---|---|
| NES, SEGA, DOS, MAME | `SOFTWARE/*`, portable installer `scripts/games/*` | **Optional** (`modules.games`) | Each becomes a `cinemediavault-module@<platform>.service`. |
| Game Boy, GBA, N64, PS1, C64, Atari 2600/5200/7800, Arcade | `DEFAULT_MODULES` in the application | **Optional** | Available; off by default, as they were upstream. |
| Library catalogue builders | `build_library.py` per platform | **Optional** (`modules.creators` = `games`) | Deduplicate and rank ROM dumps, extract payloads, emit the catalogue. |
| js-dos bundle builder | `build_bundle.py` | **Optional** | Builds playable DOS bundles. |
| DOS runtime setup | `setup_runtime.py` | **Optional** | |
| Strategy-game map generator | `build_world_paths.py` (GLOBALWAR) | **Optional** | Regenerates map geometry from the bundled GeoJSON. |
| jsnes runtime | MIT | **Downloaded at install time** | Not vendored, so provenance stays obvious. |
| EmulatorJS runtime | **GPL-3.0** | **Downloaded at install time** | Not redistributed in this package. |
| js-dos runtime | **GPL-2.0** | **Downloaded at install time** | Not redistributed in this package. |
| ROMs, BIOS images, game data | - | **Excluded, permanently** | Never downloaded, never bundled. You supply files you are entitled to run. |
| ARISTA, LOST, PIP, GLOBALWAR, MLB-NES, Earl Weaver Baseball | `SOFTWARE/*` | **Excluded from the standard install** | Self-contained static web apps tied to specific content. Add them as custom entries on the Modules page; the generic static module service will serve any of them. |

## 8. Live TV, guide and DVR

| Component | Source | Disposition | Notes |
|---|---|---|---|
| HDHomeRun discovery | in the application, plus `installer/discovery/hdhomerun.py` | **Optional** (`livetv.enabled`) | Cloud discovery and UDP broadcast, then a direct probe. Read-only: the installer never starts a channel scan, because a scan takes a tuner offline. |
| Grid guide (`/live-tv`) | in the application | **Installed with Live TV** | Six-hour horizontal grid, logos, now marker, programme details. |
| Simple guide (`/live-tv/simple`) | in the application | **Installed with Live TV** | Retained as a fallback. |
| DVR engine | `dvr_module.py` | **Optional** (`dvr.enabled`) | Schedules, recordings, series rules, conflicts, settings. Records with `ffmpeg -map 0 -c copy`, so there is no recording-time re-encode. |
| Tuner protection | in `dvr_module.py` | **Installed with the DVR** | Checks both its own sessions and the tuner's `/status.json`, so it cannot oversubscribe. `livetv.reserved_tuners` additionally keeps tuners free for live viewing. |
| Storage protection | `dvr.min_free_gb` | **Installed with the DVR** | Recording is refused below the floor. |
| Automatic deletion | `dvr.retention_days` | **Off by default** | `0` means nothing you record is ever removed automatically. |
| Unified extended EPG collector | `CineVault-EPG-Extension/docker` | **Optional** (`epg.mode` = `extended`) | Isolated Compose project on the CineVault host running upstream `iptv-org/epg`, bound to loopback by default. It preserves the tuner's authoritative ~2 days and appends approximately 12 days. |
| tvpassport enrichment overlay | `tvpassport-enrich.config.js` | **Installed with the collector** | Restores the `new`, episode-number and year fields the DVR relies on. Fail-safe: any problem leaves the upstream result untouched. |
| Safe guide merge | `epg_extend.py` | **Installed with the collector** | The tuner guide stays authoritative; collected data is appended only beyond its horizon and only when overlapping programmes agree. |
| Verified channel map | `channels.xml` | **Operator-supplied** | The shipped map was verified for one specific lineup and is not generic. Without a map the collector produces an empty guide and the tuner guide is used unchanged. |
| Webex DVR notifications | `CINEVAULT_WEBEX_WEBHOOK_URL` | **Optional** | URL stored in `secrets.env`. |

## 9. Playback

| Component | Source | Disposition | Notes |
|---|---|---|---|
| Direct play | in the application | **Installed** | Default. No transcoding, no CPU cost. |
| On-demand HLS | in the application | **Installed** (`transcode.hls_enabled`) | Only for clients that cannot direct-play. Writes to the cache directory only. |
| Hardware encoders | NVENC / QSV / VA-API | **Auto-detected** | `transcode.hls_encoder: auto` picks what the machine actually has. |
| Video Wall | in the application | **Installed** (`modules.video_wall`) | Four simultaneous streams, fullscreen, per-slot persistent positions. Live tuners never save or seek a position. |
| Persistent stream positions | `user_video_wall` table | **Installed with the wall** | Resume writes are rejected if the slot's content has since changed. |
| hls.js player library | Apache-2.0 | **Bundled** | Redistribution is permitted; the licence notice ships beside it. |
| Cast discovery | in the application | **Installed** | |
| Poster rotation cache | `poster-rotation-cache.json` | **Installed** | Six-hour default, persisted so a restart does not rotate early. |

## 10. Downloads and the Android companion

| Component | Source | Disposition | Notes |
|---|---|---|---|
| Browser downloads | in the application | **Installed** (`modules.downloads`) | Movies, episodes, whole seasons as ZIP. |
| Compressed mobile downloads | in the application | **Installed** | Size-budgeted; the server refuses a result larger than the source. |
| Android companion APK | `CineMediaVault-1.7.0-music-player.apk` | **Excluded from this package** | A signed binary is not a source artefact, and shipping one inside an installer makes its provenance unverifiable. Build it from `CineMediaVault-1.7.0-source.zip`, or copy the existing APK to the phone. The README documents both. |
| Offline index, season ZIP extraction, background music playback | in the APK | **External** | Server-side support is installed; the app itself is separate. |

## 11. Subtitles

| Component | Source | Disposition | Notes |
|---|---|---|---|
| Sidecar and embedded discovery | in the application | **Installed** | Serves `.srt` files and embedded text tracks as browser VTT. |
| SubDL lookup | `subdl_worker.py` (design carried over) | **Optional** (`subtitles.subdl_enabled`) | One exact title/year search per asset per day, capped at the documented free-tier limit. Key in `secrets.env`. |
| Whisper transcription | `subtitle-pipeline/` | **Optional** (`subtitles.whisper_enabled`) | Private virtual environment, pinned `faster-whisper`. Installed **disabled** unless explicitly started: the first pass over a full catalogue runs for days. CPU- and memory-capped, niced, lowest I/O priority. |
| Audio-event labelling | `audio_events_to_srt.py` | **Installed with Whisper** | Conservative non-dialogue labels. |
| Whisper start/stop bridge | Modules page + WSL agent | **Optional** | The Modules card is created when Whisper is installed. On this installer it controls the local `cinemediavault-subtitles.service` directly. The original cross-host bridge through a Transcode Control database is supported by pointing `integrations.transcode_control_db` at it. |
| Argos offline translation | `argostranslate` | **Excluded from the default install** | Large model downloads for a second language. Whisper's own `translate` task covers the common case. |
| Overwriting existing subtitles | - | **Excluded, permanently** | Not configurable. Generated files are written alongside, never over. |

## 12. Transcoding

| Component | Source | Disposition | Notes |
|---|---|---|---|
| On-demand HLS | see Playback | **Installed** | Safe: never touches source media. |
| Bulk library queue | `combined_transcode_orchestrator.py`, `handbrake_transcode_worker.py` | **Optional, installed stopped** (`transcode.library_queue_enabled`) | It **rewrites source media**. Installed with an empty queue and `state: stopped`, and the installer refuses to arm it even if the configuration asks. Arming is a separate deliberate command. |
| Queue watchdog, quarantine, reconciliation | `transcode_watchdog.py`, `reconcile_movie_queue.py` | **Optional with the queue** | Repeated failures are quarantined rather than retried forever. |
| Transcode Control web UI | `.20:8126` | **External** | Linked from the Modules page if you give it a URL. Not installed here; it is a separate application with its own database and accounts. |
| GPU pipeline on `.232` | `all-software/.232-transcode` | **External** | A separate host's pipeline. Its safety rules are documented in the operator guide but it is not installed. |
| Webex job notifications | `webex_job_notifications.py` | **Optional** | Folded into the single webhook setting. |

## 13. Operations

| Component | Source | Disposition | Notes |
|---|---|---|---|
| Health check and self-heal | `cinevault-lab-healthcheck.sh` | **Installed** | Rewritten as a generated script: two consecutive failures before a restart, restart cooldown, bounded probe, low-noise disk monitoring. |
| Catalogue refresh | `refresh_cinevault_sqlite.sh`, `media-library-refresh.sh` | **Installed** | `cinemediavault-refresh.timer`. Refuses to rebuild an index from an empty directory - an unmounted share cannot erase your catalogue. |
| Database backup | `cinevault_db_backup.py` | **Installed** | Nightly, through the SQLite backup API with an integrity check, pruned to `ops.db_backup_keep`. |
| Log rotation | new | **Installed** | Daily, `copytruncate` so rotation never interrupts playback. Supersedes the live host's ad hoc `rotate_cinevault_refresh_logs.sh` cron job (found 2026-09-10): the same retention/compress behaviour, generated by `s170_logrotate.py` instead of a bespoke script. |
| Virtual-schedule repair CLI | `repair_virtual_schedules.py` | **Installed** (`payload/scripts/`) | Adapted 2026-09-10 from the live-host operator tool: asks the running application to validate/extend both virtual-channel schedules without re-randomizing them, using `CINEVAULT_DB`/`CINEVAULT_PORT` from the service environment instead of a hand-parsed env file. Manual/cron use only - not run by the installer itself. |
| Modules page | in the application | **Installed** | Generated `modules.json`; operator edits to a module's URL, logo or name survive a re-run. |
| Homepage / dashboard status | in the application | **Optional** (`integrations.homepage_enabled`) | |
| Cast controller | `cinevault_cast_controller.py` | **Excluded** | Host-specific helper for a particular set of devices. |
| SQLite bootstrap | `cinevault_sqlite_bootstrap.py` | **Superseded** | The application creates and migrates its own schema; the installer only prepares the file, sets WAL, and backs up anything pre-existing. |

## 14. Promo/barker channel (optional)

| Component | Source | Disposition | Notes |
|---|---|---|---|
| Reel renderer | `generate_combined_barker.py` | **Optional** (`promo_channel.enabled`) | Renders a spoken-narration preview reel (Kokoro ONNX text-to-speech + ffmpeg) of upcoming virtual-channel programming, on a systemd timer (`installer/steps/s135_promo.py`). Added to the live host 2026-09-09; off by default here because it needs a private ~350 MB venv and two model files. |
| One-shot announcement CLI | `barker_tts_generate.py` | **Installed with the module** | Standalone Kokoro TTS helper used by `virtual_channels.py` (`CINEVAULT_BARKER_TTS_*` environment) for shorter, on-demand announcements. |
| Kokoro model weights | `kokoro-v1.0.onnx`, `voices-v1.0.bin` | **Not bundled, not downloaded** | ~353 MB combined. The operator supplies both files and points `promo_channel.model_path`/`promo_channel.voices_path` at them. See `docs/PROMO-BARKER-CHANNEL.md` and `docs/THIRD-PARTY-NOTICES.md`. |
| Wizard page | — | **Not yet built** | Configurable today only through `cinemediavault.yaml`/unattended install, not the graphical setup wizard. A known gap; see `docs/PROMO-BARKER-CHANNEL.md`. |

## 15. Explicitly excluded, with reasons

| Item | Reason |
|---|---|
| Live databases, user tables, watch history | User data. A fresh install starts empty; restore from a backup instead. |
| TLS private keys, `cinemediavault-lab.env`, API tokens, Webex tokens | Credentials. Never packaged. You supply your own during setup. |
| Media files, posters, thumbnails, HLS caches | Yours, and regenerable. |
| HDHomeRun device authorisation tokens | Device credentials. The installer records only the device ID, model, address and tuner count. |
| The `jnicolas` default account and its `admin1` password | A publicly known credential. Actively patched out; the install fails if it cannot be. |
| Host-specific IP addresses (`192.168.1.20`, `.134`, `.213`) | The original deployment's addresses. Every one is a setting now. |
| Nextcloud, Open WebUI, Ollama, Radarr/Sonarr/Prowlarr/Bazarr, Portainer, qBittorrent | Separate applications on other hosts. Out of scope; link to them from the Modules page. |
| `arr-download-cleanup.py` | Deletes files under a downloads directory. Belongs to the Docker host, not here. |
| Bridgette private vault | A separate private application with its own access rules. |
| Windows/PowerShell tooling | Workstation-side. |
| `sotware.tar`, `DOS.tar`, ROM archives | Bulk binary content, much of it third-party. |

## 16. Coverage against the original request

| Requested | Where it is |
|---|---|
| Core web application | §1 |
| Movies with metadata and posters | §2 |
| TV with seasons, episodes, thumbnails | §3 |
| Music with persistent/background playback | §4 |
| Book Vault, ebooks and audiobooks | §5 |
| Comics library | §6 |
| Live TV and HDHomeRun discovery | §8 |
| Grid EPG, iptv-org collector, verified mapping, safe merge | §8 |
| DVR: schedules, recordings, series, conflicts, storage, tuner protection | §8 |
| Video Wall and persistent stream positions | §9 |
| Downloads, offline support, Android packaging | §10 |
| Subtitles: existing, SRT, SubDL, Whisper | §11 |
| Modules page and the Whisper start/stop bridge | §11, §13 |
| Media scans, periodic refresh, self-healing | §13 |
| Movie, TV and music transcoding, no destructive queue by default | §12 |
| Game and study-game generation tools | §7 |
| Comic-book creation tools | §6 |
| Book creation tools | §5, §6 (the magazine/collection hub generator serves both) |
| Genre discovery (Movies and TV) | §1 |
| Ten virtual movie channels + ten virtual TV channels, 14-day guide | §1 |
| Promo/barker spoken-preview channel | §14 |
| Everything else found in `all-software` | §15 |
