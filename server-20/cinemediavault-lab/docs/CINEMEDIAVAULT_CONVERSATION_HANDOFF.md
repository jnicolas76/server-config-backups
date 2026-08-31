# CineMediaVault Project Handoff

Detailed verified host/IP roles, media mounts, Docker compose locations and ports,
canonical CineVault paths/modules, both transcode pipelines, queue/state/log files,
schedules, and safety rules are maintained in:
`C:\Data\HOMELAB_CINEVAULT_TRANSCODE_DOCKER_REFERENCE_2026-08-30.md`.

Last updated: 2026-08-18 (America/Denver)

This document is the continuity record for the long-running CineMediaVault,
transcoding, comic, book, game, music, Webex, and server-management project.
Use it to resume work if the original conversation is compacted or unavailable.

## Primary Hosts

- Windows/WSL workstation: NVIDIA RTX 4090; performs HandBrake/FFmpeg jobs.
- `192.168.1.20`: CineMediaVault production/lab services and BookVault.
- `192.168.1.121`: Raspberry Pi storage host exporting Movies and TV Shows.
- `192.168.1.134`: Docker/Homepage host and some game/server workloads.
- `192.168.1.19`: Plex host.

## Storage And Mounts

- WSL movies: `/mnt/nfs-share-movies`
- WSL TV shows: `/mnt/nfs-share-tvshows`
- Movie library: `/mnt/nfs-share-movies/Movies`
- TV library: `/mnt/nfs-share-tvshows/TV Shows`
- Local work root: `/mnt/c/DATA`
- Portable software root: `/mnt/c/DATA/SOFTWARE/LINUX`
- Original files are normally archived in the applicable `COMPLETED` folder
  after replacement files are verified and moved back to their source folders.

## CineMediaVault

- Production: `http://192.168.1.20:8093`
- Lab: `https://192.168.1.20:5000`
- Production and lab use separate databases and caches.
- Features include movies, TV shows, posters, TMDb metadata, actors, genres,
  direct/HLS playback, watch progress, users, admin tools, downloads, compressed
  mobile downloads, video wall, tuner discovery, casting discovery, modules,
  API statistics, and an Android APK with offline support.
- The master/admin user is `jnicolas`. Do not record plaintext passwords here.
- Portable installers and README material are maintained under `/mnt/c/DATA/SOFTWARE`.

## Supporting Libraries

- BookVault has run on `.20`, commonly port `8112`.
- Comic library lives on the Movies storage and is linked as a CineMediaVault module.
- Emulator modules include NES, Sega/Genesis, DOS, MAME, and related game libraries.
- Bridgette private vault has used port `8097` and local WSL media.

## Transcoding

- Main WSL orchestrator: `/mnt/c/DATA/combined_transcode_orchestrator.py`
- Agent: `/mnt/c/DATA/transcode-control-agent/agent.py`
- Shared configuration: `/mnt/c/DATA/transcode-control-config.json`
- Movie staging: `/mnt/c/DATA/HANDBRAKE-H265`
- TV staging: `/mnt/c/DATA/HANDBRAKE-TV-H265`
- Transcode Control: `http://192.168.1.20:8126`
- Portable package: `/mnt/c/DATA/SOFTWARE/LINUX/transcode-control`
- Current controller supports configurable library paths, H.264/H.265, MP4/MKV,
  exact target size or GB/hour, presets, audio bitrate, eligibility rules,
  retries, queue ordering, FFprobe details, and worker telemetry.
- It now includes SQLite-backed PBKDF2 password management and an Account page.
- Analytics now include savings history, queue mix, codec/container distribution,
  storage utilization, CPU/load/worker telemetry, measured output, and reduction.
- Live controller commit for that upgrade: `c6299e6`.
- Existing combined orchestration is intentionally single-worker unless explicitly
  redesigned with isolated scratch directories and a real dispatcher.

## Webex

- WSL has Webex status/listener integration for job, queue, disk, and recovery updates.
- Sensitive actions use a five-digit verification challenge.
- Only the authorized Jonathan Nicolas identity should be allowed to control it.
- Webex secrets and tokens must never be copied into documentation or Git.

## Backup And Installation

- Private GitHub repository used previously:
  `git@github.com:jnicolas76/server-config-backups.git`
- A recent GitHub push failed because the currently available SSH key was rejected.
  Local live code and portable package remained updated; repair GitHub key access
  before relying on remote backup.
- Never include live databases, credentials, tokens, caches, or media in clean
  installer packages.

## Immediate Next Phase: Music In CineMediaVault

The user wants Music added as a first-class CineMediaVault library. Build this
incrementally in the lab instance before production.

Recommended scope:

1. Add configurable Music root(s) in admin Modules/Settings and installers.
2. Scan audio without modifying source files.
3. Store artists, album artists, albums, tracks, disc/track numbers, year, genres,
   duration, codec, bitrate, sample rate, channels, file size, path, and timestamps
   in SQLite.
4. Read tags with Mutagen or FFprobe; use folder/filename inference only as fallback.
5. Cache embedded artwork first; support optional external metadata/artwork lookup.
6. Add Artists, Albums, Tracks, Genres, Recently Added, Search, and Continue Listening.
7. Add per-user play history, favorites, playlists, resume position for long audio,
   and watched-equivalent completion state.
8. Provide direct playback first; only transcode audio when the client cannot play it.
9. Add download/offline support to the APK after the browser implementation is stable.
10. Expose music counts, storage, active playback, and recent additions through the
    existing CineMediaVault API and Homepage integration.
11. Update production, lab, APK, portable installers, startup scripts, README files,
    API documentation, and Git backup after validation.

## Safety Rules

- Do not delete, replace, rename, or transcode source media without validation.
- Preserve unrelated user changes and dirty worktrees.
- Test in port `5000` lab before changing production port `8093`.
- Back up databases before schema migrations.
- Use atomic writes and transaction-backed migrations.
- Do not interrupt an active encode merely to update the dashboard.
- Validate mounts before scans or jobs to avoid treating an unavailable NFS mount
  as an empty library.

## Update - 2026-08-30: Canonical CineVault And Current Repair

- The lab instance on `192.168.1.20:5000` was elevated to be the sole canonical
  live CineVault instance. Do not assume the old `8093` production tree is active.
- The canonical wrapper is
  `/home/jnicolas/cinemediavault-lab/cinemediavault-lab-5000.py`.
- Its active compatibility movie module is
  `/home/jnicolas/cinemediavault-lab/media-download-library/media_download_server.py`.
  A similarly named file directly under `cinemediavault-lab` is not the module
  loaded by the promoted wrapper.
- Current issue: repeated Fix Match attempts for `The Long, Long Trailer` appeared
  to revert after catalog refresh. Continue Watching is not rewriting metadata.
- Two causes were identified:
  1. Continue Watching persisted the old scan-order numeric movie ID `5390`; the
     same file currently resolves to numeric ID `5395`. Its stable media key is
     `movie-path:c7c468cc3cf089972e52e662`.
  2. Durable manual metadata/poster override support was previously copied into
     the wrong compatibility module, so the active scanner could overwrite the
     base generated maps during refresh.
- Correct durable override data already exists on `.20` in:
  `/home/jnicolas/cinemediavault-lab/media-download-library/manual-metadata-overrides.json`
  and `manual-poster-overrides.json`.
- Correct override identity: relative path
  `The Long Long Trailer Lucille Ball (1953)/The Long Long Trailer Lucille Ball (1953).mp4`,
  TMDb ID `24518`, canonical title `The Long, Long Trailer`, release date
  `1954-02-19`, poster `posters/tmdb-24518-w500.jpg`.
- The wrapper now contains a stable saved-ID resolver for movie routes and updates
  stale Continue Watching IDs/hrefs to the current catalog ID.
- Completed on 2026-08-30: the corrected local `lab-media-download-server.py`
  was copied to the active module path on `.20`, compiled successfully, and the
  canonical port 5000 service was restarted successfully as PID `1146253`.
- The previous live module was preserved as
  `/home/jnicolas/cinemediavault-lab/media-download-library/media_download_server.py.bak-manual-overrides-20260830-113258`.
- Post-deployment validation resolved the stale saved ID `5390` to current ID
  `5395` and loaded TMDb `24518`, title `The Long, Long Trailer`, year `1954`,
  the correct poster, plot, genres, and cast from the durable override layer.
- A separate legacy WSL transcode queue for 20 newly added Scooby-Doo episodes is
  running independently. Its target is about 150 MiB per episode. Do not interrupt
  it while restarting CineVault on `.20`. Queue files/logs are under
  `/mnt/c/DATA/handbrake-scooby-new-20260830-*`.
## Music Module Update - 2026-08-18

- Main library: `/media/jnicolas/Expansion/Music`
- Production: `http://192.168.1.20:8093/music`
- Lab: `https://192.168.1.20:5000/music`
- Module source: `music_module.py`
- Features: incremental ffprobe scan, artist/album/track/genre search, embedded/folder artwork, byte-range streaming and seeking, shuffle/repeat/queue, persistent per-user playlists, track downloads, and playlist ZIP downloads with audio, artwork, and `manifest.json` for offline clients.
- Separate databases are used by production and lab.
- Portable installer under `C:\DATA\cinevault-portable-installer` includes Music root configuration, ffmpeg dependency installation, separate databases/art caches, the Music server module, and updated production/lab application files.
- Last verified catalog counts: lab 9,100 tracks / 468 artists / 1,458 albums; production scan active at 1,300 tracks / 78 artists / 221 albums.

## Update - 2026-08-31: Permanent Movie Match Persistence

- The user reported that previously corrected matches for `Home on the Range
  (2004)` and `Motor City (2026)` again displayed without their posters/metadata.
- The on-disk automatic maps still contained the correct records and poster files.
  CineVault started at approximately 11:16, while the scheduled refresh replaced
  the maps at approximately 11:18. The imported movie module held stale in-memory
  dictionaries and did not see those new files.
- A second weakness was that legacy Fix Match overrides used only the exact relative
  video path. A transcode or rename such as `.mkv` to `.mp4` could orphan an override.
- The active module on `.20` now watches automatic and manual metadata/poster map
  signatures and reloads changed maps automatically (throttled to one check every
  two seconds).
- Manual overrides are held separately and always take precedence over automatic
  metadata.
- Fix Match and custom poster uploads now store both the exact relative path and a
  stable `asset:<normalized-movie-folder>` identity. Legacy exact-path overrides are
  migrated to stable identities at startup.
- Home on the Range is durably pinned to TMDb `13700` under
  `asset:homeontherange2004`; Motor City is pinned to TMDb `87513` under
  `asset:motorcity2026`.
- Active source:
  `/home/jnicolas/cinemediavault-lab/media-download-library/media_download_server.py`.
- Pre-change backup:
  `/home/jnicolas/cinemediavault-lab/backups/stable-movie-matches-pre-20260831-114000`.
- Post-change backup:
  `/home/jnicolas/cinemediavault-lab/backups/stable-movie-matches-post-20260831-114500`.
- The canonical wrapper invokes the manual-override migration during startup and
  media-state reloads. Its first verified startup migrated 10 legacy overrides.
- CineVault's final restart succeeded as PID `1386493`; HTTPS health check returned
  `200`. Direct verification returned the expected TMDb IDs, titles, and posters for
  both repaired assets.
- Detailed recovery note: `C:\Data\CINEVAULT_STABLE_MOVIE_MATCHES_2026-08-31.md`.

## Update - 2026-08-31: Full Music Player and Android Background Playback

- The canonical CineVault music module on `.20` is
  `/home/jnicolas/cinemediavault-lab/music_module.py`.
- The music database is `/home/jnicolas/cinevault-data/music.db`; the verified
  catalog contains 97,041 tracks.
- The web music interface now has a full-screen Now Playing view with large art,
  elapsed/duration and seek controls, previous/play/next, queue, shuffle, repeat,
  and Cast.
- Browser playback exposes Media Session actions and metadata for browsers that
  support lock-screen/media controls.
- Per-user resume state is stored in SQLite table `music_playback_state`. The
  authenticated API is `GET/POST /api/music/now-playing`; it stores track, position,
  queue/index, shuffle, repeat, and playing state. Restored state does not autoplay.
- The server saves state periodically and on visibility/page-exit events. Queue
  input is validated and limited to 500 entries.
- Android release `CineMediaVault-1.7.0-music-player.apk` keeps the 1.6.1 offline
  and download features and adds a native foreground `MusicPlaybackService`, native
  MediaSession/notification controls, persistent queue/position, and audio-aware
  Cast handling.
- Android package remains `com.cinevault.companion`; release is versionCode `27`,
  versionName `1.7.0`. Its signing certificate matches 1.6.1, so it installs as an
  upgrade. APK SHA-256 is
  `1efda976263eeb4fa5b4d4df9548c281389cdf9dd8d003e7055b88addcd10bf0`.
- Nextcloud package directory: `C:\Users\jonat\Nextcloud\CineVault`.
- Server `.20` package directory: `/home/jnicolas/cinemediavault-lab/apk`.
- Both directories contain the 1.7.0 APK, 1.6.1 rollback APK, source ZIP, and README.
- Pre-change backup:
  `/home/jnicolas/cinemediavault-lab/backups/music-player-full-pre-20260831-131500`.
- Post-change backup:
  `/home/jnicolas/cinemediavault-lab/backups/music-player-full-post-20260831-162400`.
- Detailed Windows README: `C:\Data\CineMediaVault-1.7.0-MUSIC-README.md`.
- Android source archive: `C:\Data\CineMediaVault-1.7.0-source.zip`.
- Live service restarted successfully after the web deployment and returned HTTP
  200 at `/login`. Python, API state tests, JavaScript syntax, Android Gradle build,
  APK metadata, signature, and copied checksum were verified.
