# CineMediaVault Production Release — 2026-09-09

This release snapshot documents and preserves the CineMediaVault instance running on server `192.168.1.20` at `https://192.168.1.20:5000/`.

## Included functions

- Responsive movie, television, and music libraries for Android browsers, Apple browsers, desktop browsers, and the Samsung Tizen client.
- Persistent user accounts, theme selection, playback progress, continue-watching state, recently-added tracking, metadata, posters, captions, and audio-stream selection.
- Direct playback when the browser supports the source video and audio; HLS conversion when compatibility requires it.
- HDHomeRun Live TV, consolidated EPG, DVR recording, recording playback, schedules, and series rules.
- Twenty simulated 24×7 channels: ten movie channels and ten television channels.
- Rolling fourteen-day virtual schedules, chronological TV episode progression, guide navigation, Watch Live offsets, Play from Beginning, holding screens, and Up Next transitions.
- Guide mini-previews that start only while the guide is visible and are released when leaving the guide or launching playback.
- Movie and TV promotional barkers built from the upcoming 48-hour schedule. Barkers exclude current and past programs, refresh every 15 minutes and just after midnight, show date/time/channel/poster/summary, and play one audible 30-second preview at a time after user activation.
- Virtual-channel administration for schedule status, flush, repair, rebuild, and re-randomization.
- Server activity, per-user stream visibility, CPU/bandwidth history, and diagnostics.

## Release contents

- `cinemediavault-lab-5000.py` — primary HTTPS application and playback service.
- `virtual_channels.py` — virtual schedule, guide, preview, barker, and playout module.
- `dvr_module.py` — Live TV DVR module.
- `music_module.py` — music library and player module.
- `media_download_server.py` — movie library module.
- `tv_download_server.py` — television library module.
- `test_virtual_channels.py` — regression suite for virtual scheduling and playback.
- `repair_virtual_schedules.py` — schedule repair utility.
- `start_lab_5000.sh` and `stop_lab_5000.sh` — service wrappers.
- `install_cinemediavault_release.sh` — installs this source snapshot into a selected application directory.

Runtime databases, TLS private keys, passwords, tokens, logs, generated HLS segments, caches, and media files are deliberately excluded from GitHub and the portable source bundle.

## Installation

Install Ubuntu/Debian prerequisites:

```bash
sudo apt-get update
sudo apt-get install -y python3 ffmpeg sqlite3
```

Copy the release directory to the destination host, then run:

```bash
chmod +x install_cinemediavault_release.sh
./install_cinemediavault_release.sh /home/jnicolas/cinemediavault-lab
```

Review the environment/configuration paths and provide the target server's own TLS certificate, database, media mounts, HDHomeRun address, EPG settings, and API credentials. Never copy production secrets to a new test VM.

Start and verify:

```bash
/home/jnicolas/cinemediavault-lab/start_lab_5000.sh
python3 -m unittest -q /home/jnicolas/cinemediavault-lab/test_virtual_channels.py
curl -k -I https://127.0.0.1:5000/
```

## Operations

```bash
# Start
/home/jnicolas/cinemediavault-lab/start_lab_5000.sh

# Stop
/home/jnicolas/cinemediavault-lab/stop_lab_5000.sh

# Syntax check
python3 -m py_compile /home/jnicolas/cinemediavault-lab/cinemediavault-lab-5000.py /home/jnicolas/cinemediavault-lab/virtual_channels.py

# Virtual-channel regression tests
cd /home/jnicolas/cinemediavault-lab
python3 -m unittest -q test_virtual_channels.py
```

Use the CineVault Admin page to flush or rebuild both virtual schedules. Rebuilding re-randomizes the rolling lineup. The application continuously extends its schedule horizon for year-round operation.

## Barker behavior

The browser requires a user gesture before audible autoplay, so select **Start previews with sound** once. The barker then rotates through future programming. A server-side check rejects programs once their airtime begins, while the browser prunes expired entries before every clip. Incompatible source audio is converted to AAC; compatible files may play directly.

## Backup and restore

Stop the application before restoring source. Copy the desired release files over the application directory, retain the destination server's configuration and secrets, run the syntax/tests above, then restart. Restore a database only from a coordinated database backup; source restoration does not require replacing user or library data.

