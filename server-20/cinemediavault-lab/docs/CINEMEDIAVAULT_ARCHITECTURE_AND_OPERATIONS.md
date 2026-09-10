# CineMediaVault Architecture and Operations

**Release date:** September 9, 2026

**Deployment:** 192.168.1.20 - HTTPS port 5000

## Executive overview

CineMediaVault is a private, self-hosted media platform running on server 192.168.1.20 and served over HTTPS on port 5000. It unifies Movies, TV Shows, Music, physical Live TV, DVR, virtual linear channels, search, user profiles, playback history, recommendations, administration, and Samsung Tizen access.

The design favors direct playback when a client supports the stored container, video codec, and audio codec. HLS and FFmpeg are compatibility fallbacks. Persistent user state and schedules live in SQLite; media, generated previews, metadata caches, and artwork live on mounted storage and application data paths.

This document describes the deployed September 2026 design. Secrets, passwords, private keys, API tokens, and media contents are intentionally omitted.

## System context

Primary host: 192.168.1.20. Canonical application directory: /home/jnicolas/cinemediavault-lab. Public application endpoint: https://192.168.1.20:5000/. The host launches the stack through start_everything.sh and start_lab_5000.sh, with a minute-level health watchdog.

Clients include Android Chrome, Apple Safari, desktop browsers, and the Samsung Tizen application. All clients use the same server APIs and user database, so themes, progress, recently added state, and Continue Watching can follow the authenticated user between devices.

Media is read from configured movie, television, music, recording, and book/comic legacy mounts. The current primary experience exposes Movies, TV Shows, Music, Live TV, and DVR.

## Major components

cinemediavault-lab-5000.py - HTTPS entry point, authentication, routing, home UI, search, playback, administration, HLS orchestration, and module integration.

media_download_server.py - movie discovery, metadata, posters, detail pages, streams, and playback sources.

tv_download_server.py - television/show/season/episode discovery and metadata resolution.

music_module.py - music browsing and playback.

dvr_module.py - HDHomeRun live television, recording schedules, series rules, recordings, and playback.

virtual_channels.py - virtual movie/TV channel definitions, 14-day schedules, guide UI, channel tuning, clock offsets, direct/HLS selection, mini-previews, Up Next transitions, promotional barker integration, and schedule administration.

genre_catalog.py - normalized genre/category mapping used by browsing and virtual channel assignment.

cinevault_theme.py - user-selectable themes persisted in the database.

cinevault_usage.py - active and historical bandwidth, stream, CPU, and per-user usage collection.

barker_tts_generate.py and generate_combined_barker.py - offline speech and six-hour promotional reel production.

## Request and playback flow

A client authenticates with CineVault and requests a library, guide, detail page, or search result. The main application routes the request to the relevant module and resolves the authenticated user's preferences and progress.

For playback, CineVault probes the source. Compatible browser media uses a direct byte-range stream. Sources with unsupported containers or audio are routed to HLS, where FFmpeg creates a browser-compatible H.264/AAC stream. Audio PID/track selection is carried into the source resolver. Captions are attached when available and can be turned on or off.

Progress updates are stored against the user and exact media item. Continue Watching derives from persistent progress rather than transient browser state. Playback completion removes or advances the appropriate entry.

## Libraries and discovery

Movie and TV scanners maintain normalized library indexes and TMDB-backed metadata/poster caches. Manual override maps exist for ambiguous titles. The Hunter correction illustrates the rule: ambiguous title-only matches must be pinned to an exact external identity; Hunter is mapped to the live-action 1984 series rather than the unrelated anime.

Search spans Movies and TV Shows. Genre/category browsing uses normalized TMDB genre data. Recently Added is derived from a persistent ledger so a restart does not reorder the library merely because files were rescanned. Recently Released is distinct from Recently Added.

TV metadata follows show, season, and episode identity. Display text is normalized to human-readable forms such as Season 1, Episode 2. Posters, summaries, ratings, dates, and episode titles are kept with the selected media identity.

## Physical Live TV, EPG, and DVR

Physical Live TV uses the configured tuner/HDHomeRun source. The EPG pipeline combines the near-term local guide with the extended provider data into one guide. DVR supports one-time recordings, schedules, series rules, completed recordings, playback, downloads, and deletion.

The guide and DVR are server-side capabilities shared by browser and supported app clients. Recording and tuner availability remain constrained by source hardware and simultaneous tuner capacity.

## Virtual channels

Virtual Movies and Virtual TV simulate linear channels without continuously decoding every channel. A rolling 14-day SQLite schedule provides channel, start, stop, title, media identity, poster, summary, and rating. Tuning computes the clock offset and begins at the point that would be live now. Play from Beginning uses normal on-demand playback.

Movie channels cover Action, Romance, Comedy, Science Fiction, Horror, Thriller, Drama, Film Noir, Kids and Family, and International. TV includes the core genre channels plus Knowledge, The Simpsons, The Zone, Nostalgia, and Sitcom. TV progress rules can maintain chronological episodes where required while specialty channels can use controlled randomization.

The guide keeps channel names frozen while the timeline scrolls. It shows current and future programs, ratings, posters, summaries, off-air holding information, and Watch Live/Play from Beginning actions. Miniature per-channel previews are optional and are created only while visible; leaving the guide or starting playback tears them down.

Virtual channel playback is deliberately linear and uncontrollable: native transport controls are absent, pause/stop/rewind/fast-forward/media-key attempts are ignored, and an attempted seek is corrected to the wall-clock live offset. Audio PID/track and captions on/off remain available. Play from Beginning exits the linear channel and opens the title through normal on-demand playback with standard controls.

At a program boundary the player exits presentation mode for a ten-second Up Next panel, shows the next title/poster/episode information, swaps the source in the same video element, and returns to full-screen presentation. Reusing the video element is necessary to preserve browser fullscreen state as far as the platform allows.

## Guide barker and active-player handoff

The guide contains a sticky barker panel: video at left and synchronized poster, title, airtime, channel, episode, and summary at right. The panel is an opaque isolated stacking layer, so guide rows scroll behind it. The former blue segment timer is removed.

When no virtual channel is active, the barker plays the clock-synchronized promotional reel. When a viewer opens the guide from a playing virtual channel, CineVault suspends the promo reel and moves the existing player session into the barker's video rectangle. The right side switches to NOW PLAYING and uses metadata from that exact tuned program. No second stream is opened and the playback position is retained. Selecting the video returns to the full player. Closing the active session allows the promo reel to resume.

Remote playback/Cast controls are disabled on the barker and embedded-guide player. Native browser autoplay policy still has final authority over unmuted autoplay: CineVault first requests audible autoplay and falls back to muted playback only when the browser blocks it. A user gesture can enable sound. Installed TV applications can provide a stronger autoplay guarantee than general-purpose browsers.

## Promotional reel generation

A low-priority FFmpeg job builds a 60-minute H.264/AAC reel from future Movie and TV schedule entries. Each one-minute segment uses a local clip plus offline Kokoro narration. Announcements vary their wording, pronounce season/episode notation naturally, and use upcoming date, time, channel, cast (for movies when available), and stored summary.

The job runs at 5:05, 11:05, 17:05, and 23:05 local time, one hour before the next six-hour slot. It uses flock to prevent concurrent builders and nice/ionice to reduce production impact. The current published reel remains active until a replacement renders, validates, and is atomically published.

The sidecar programme list is built only from successfully rendered segments. This prevents a failed clip from shifting the video relative to the title, poster, or narration. Playback joins the reel at a clock-derived position and loops.

## User experience and clients

Responsive layouts support phone, tablet, desktop, Apple browser, Android browser, and Samsung Tizen screen sizes. Remote/keyboard focus styles and directional navigation are included for television use.

Themes include the original palette, royal blue, and green. The selection is stored per user in SQLite so it follows the account between devices and is applied to buttons and all integrated pages.

The Samsung Tizen client is a signed application shell using the CineVault HTTPS APIs. Each television must be authorized by its device UID in the distributor certificate used for deployment. Application packages must be rebuilt and redeployed when client-side Tizen code changes; purely server-rendered web changes appear without rebuilding when the app loads those pages.

## Data and persistence

The canonical application database is /home/jnicolas/cinemediavault-lab/cinevault-data/cinemediavault-lab.db. Additional SQLite files and JSON caches support modules, metadata, poster rotation, guide extension, recently-added state, and legacy compatibility. SQLite WAL-safe backup tooling is used for consistent database copies.

Persistent data includes users, sessions, preferences, themes, playback progress, Continue Watching, virtual definitions/schedules/episode progress, DVR state, metadata overrides, and usage history. Generated HLS and temporary preview sessions are disposable runtime data.

Backups exclude credentials from source control. TLS private keys, passwords, session secrets, API tokens, and production-only configuration must be restored separately from protected storage.

## Operations and automation

Startup: /home/jnicolas/cinemediavault-lab/start_lab_5000.sh. Stop: stop_lab_5000.sh. Full host startup: /home/jnicolas/start_everything.sh. Health watchdog: cinevault-lab-healthcheck.sh every minute.

Library refresh runs every 15 minutes. SQLite refresh runs every 15 minutes. A consistent database backup runs nightly at 1:00 AM. The promotional reel builder runs four times daily. Use the Admin page to inspect schedule status, flush both virtual schedules, repair, rebuild, and re-randomize.

Validation commands: python3 -m py_compile cinemediavault-lab-5000.py virtual_channels.py; python3 -m unittest test_virtual_channels -q; curl -k -I https://127.0.0.1:5000/. Current virtual-channel regression count: 35 tests.

## Monitoring and diagnostics

The Admin activity view reports active streams, mode (Direct/HLS), user, aggregate bandwidth, CPU, memory, and historical usage over selectable time ranges. Stream lifecycle cleanup prevents stopped simulators or abandoned sessions from inflating active counts.

FFmpeg diagnostics identify active processes, source, output mode, elapsed time, CPU/memory, and session ownership. Mini-preview and HLS resources are explicitly released when navigating away.

## Installation and recovery

The release installer copies source into a selected target directory, creates timestamped pre-install backups, validates Python syntax, provisions the local Kokoro environment/model when online, and preserves destination-specific database, TLS, mounts, and credentials.

For a clean VM test, install Python 3, venv, FFmpeg, SQLite, and curl; mount media; provide protected TLS and configuration; run install_cinemediavault_release.sh; start the service; run regression tests; then verify login, direct/HLS playback, progress persistence, Live TV/DVR, virtual guide offsets, audio selection, and client layouts.

Rollback source by stopping CineVault, restoring a timestamped source backup, compiling/testing, and restarting. Restore SQLite only from a verified coordinated backup. Do not replace a healthy database merely to roll back UI source.

## Security and backup policy

CineVault is served via HTTPS and requires authentication for private library functions. Access should remain limited to trusted LAN/VPN paths unless a hardened reverse proxy, rate limits, current certificates, firewall rules, and monitored authentication are in place.

Source and safe configuration are backed up to the server-config-backups Git repository. Runtime data and coordinated SQLite snapshots are archived locally and to Nextcloud. GitHub receives no media, databases, credentials, private keys, tokens, generated HLS, TTS cache, or large generated reels.

Every release backup includes checksums and a manifest. A backup is not considered complete until its archive is readable, its checksum verifies, and the source regression tests pass.

## Known platform constraints

General-purpose browsers can refuse audible autoplay even when autoplay is requested. CineVault attempts sound first and falls back safely; a prior user gesture or installed-app policy is required for an absolute guarantee.

Native browser fullscreen cannot be silently reacquired after a navigation. CineVault minimizes navigation and reuses the same media element across virtual transitions. Device codec support determines whether Direct playback is possible.

Title-only metadata matching is unsafe for ambiguous works. Manual/exact IDs and path-based episode identity are the required correction mechanism.

## Release verification checklist

1. HTTPS home and login respond. 2. Movies, TV Shows, Music, Live TV, and DVR populate. 3. Continue Watching survives restart. 4. Direct and HLS samples play with audio and captions. 5. Audio track switching and CC on/off work. 6. Virtual Movie and TV guides cover the rolling horizon. 7. Watch Live joins at the correct clock offset and blocks transport/seek controls. 8. Play from Beginning opens on-demand playback. 9. Guide mini-previews release on navigation. 10. Active playback replaces the guide barker without a second stream. 11. Barker has no blue timer or Cast control. 12. Metadata matches the exact clip. 13. Admin metrics and schedule operations work. 14. All virtual-channel tests pass. 15. Backup checksum verifies.
