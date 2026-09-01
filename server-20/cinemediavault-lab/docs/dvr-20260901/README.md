# CineMediaVault Live TV & DVR

Deployed 2026-09-01 on `192.168.1.20:5000` for HDHomeRun EXTEND `105935AB` at `192.168.1.213`.

## User interface

- `/live-tv` — six-hour horizontal grid guide with channel logos, current-time marker, earlier/later navigation and program details.
- `/dvr/schedule` — scheduled and active recordings.
- `/dvr/recordings` — completed/failed history with Play, Download and Delete controls.
- `/dvr/series` — active or paused series rules.
- `/dvr/conflicts` — conflicts, failures and missed recordings.
- `/dvr/settings` — recording path, start/end padding and retention configuration.
- `/live-tv/simple` — the previous vertical Live TV page retained as a fallback.

## Recording behavior

- Records the HDHomeRun MPEG transport stream using `ffmpeg -map 0 -c copy`; there is no recording-time re-encode or meaningful CPU load.
- Default storage: `/media/jnicolas/Expansion/CineVault DVR`.
- Organizes recordings into Movies, TV Shows, Sports and News, then title folders.
- Default padding: 60 seconds before and 120 seconds after.
- Automatic deletion is disabled by default (`retention_days=0`).
- One-time, all-episode and new-episode-only rules are supported.
- Duplicate programme keys prevent the same broadcast from being scheduled twice.
- The scheduler checks the HDHomeRun's live tuner status as well as CineVault sessions. The two-tuner EXTEND will not start a third tuner job.
- Active recordings persist in SQLite and interrupted jobs are recovered or marked failed after a service restart.
- Optional Webex start/completion/conflict messages are enabled when `CINEVAULT_WEBEX_WEBHOOK_URL` is set in the service environment.

## Playback

Live channels and completed recordings use CineVault's existing HLS player. Original `.ts` recordings are also available through Download.

## Database

The active database is `/home/jnicolas/cinemediavault-lab/cinevault-data/cinemediavault-lab.db` and contains:

- `dvr_settings`
- `dvr_series_rules`
- `dvr_recordings`
- `dvr_tuner_sessions`
- `dvr_events`

## Source and service files

- `/home/jnicolas/cinemediavault-lab/dvr_module.py`
- `/home/jnicolas/cinemediavault-lab/cinemediavault-lab-5000.py`
- `/home/jnicolas/cinemediavault-lab/hdhr-guide-cache.json`

## Validation

- Python compilation passed.
- Embedded guide JavaScript passed Node syntax checking.
- Authenticated HTTP 200 returned for all six DVR pages and both DVR read APIs.
- A real end-to-end scheduler test recorded 7.5 MB from channel 4.1, finalized successfully, opened through the recording player and released its tuner.
- Test recording, file, schedule and event records were removed afterward.
- Current guide: 7,511 programs on 116 guide-supported channels, 111 channel logos and 940 programs marked new.

## Backups

- Pre-change: `/home/jnicolas/cinemediavault-lab/backups/20260901-dvr-pre`
- Post-change source, active SQLite database, and guide-cache snapshot: `/home/jnicolas/cinemediavault-lab/backups/20260901-dvr-post`

## Notes

The current free HDHomeRun guide tier supplies roughly two days. An active HDHomeRun DVR guide subscription can extend the same grid and series scheduler to approximately 14 days without a code change.
