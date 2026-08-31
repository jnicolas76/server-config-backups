# CineMediaVault Logo and Mobile Action Cleanup

Deployed to the canonical CineMediaVault instance on `192.168.1.20:5000` on
2026-08-31.

## Changes

- Restyled the CineMedia portion of the CineMediaVault wordmark in cursive.
- Preserved the bold gold VAULT treatment, divider, and film-reel mark.
- Replaced the ambiguous circular start-over control with a labeled Restart
  button.
- Reduced movie primary actions to Mark Watched, Download, and More.
- Reduced TV episode primary actions to Mark Watched, Download, Next (when
  available), and More.
- Added a mobile bottom sheet for secondary actions.
- Movie More menu: Fix Match, Cast & Crew, and Admin for administrators.
- Episode More menu: Show, Season, bulk watched actions, Previous Episode when
  available, Cast & Crew, and Admin for administrators.
- Kept Cast & Crew available as its own menu entry instead of overloading the
  More button.

## Live software

- Host: `jnicolas@192.168.1.20`
- Directory: `/home/jnicolas/cinemediavault-lab`
- Main application: `cinemediavault-lab-5000.py`
- Start: `/home/jnicolas/cinemediavault-lab/start_lab_5000.sh`
- Stop: `/home/jnicolas/cinemediavault-lab/stop_lab_5000.sh`

## Backups

- Before: `/home/jnicolas/cinemediavault-lab/backups/ui-logo-actions-pre-20260831-073137`
- After: `/home/jnicolas/cinemediavault-lab/backups/ui-logo-actions-post-20260831-074305`

Both backup directories contain checksums, the application source, start/stop
scripts, environment configuration, and a consistent SQLite database backup.
The post-change backup also contains Claude's approved logo and layout design
files.

## Restore

Stop CineVault, copy the desired backed-up `cinemediavault-lab-5000.py` over
the live file, preserve executable permissions, and start CineVault again.
Only restore `cinemediavault-lab.env` or `cinevault.db` when configuration or
database rollback is also intended.

## Verification performed

- Python compilation succeeded locally and on server `.20`.
- CineVault restarted successfully with the updated source.
- `/login` returned HTTP 200 and contained the new cursive wordmark CSS.
- Authenticated client traffic resumed, including home, poster, Continue
  Watching, video-wall HLS, and live tuner HLS requests.
- No new application exception appeared after restart.
