# CineVault Single-Instance Promotion

Promotion date: 2026-08-30

## Canonical service

- URL: `https://192.168.1.20:5000`
- Application: `/home/jnicolas/cinemediavault-lab/cinemediavault-lab-5000.py`
- Movie module: `/home/jnicolas/cinemediavault-lab/media_download_server.py`
- Database: `/home/jnicolas/cinemediavault-lab/cinevault-data/cinemediavault-lab.db`
- Startup wrapper: `/home/jnicolas/cinemediavault-lab/start_lab_5000.sh`
- Health check: `/home/jnicolas/cinemediavault-lab/cinevault-lab-healthcheck.sh`

The directory names retain `lab` for compatibility, but this is now the only live CineVault application. The former application on port 8093 is stopped and disabled by default in `/home/jnicolas/start_everything.sh`. It can be started temporarily for rollback by setting `CINEVAULT_LEGACY_ENABLED=1`.

The Homepage entry on server 134 points to the canonical HTTPS URL and its `/api/homepage/status` endpoint.

## Media refresh

The existing production scanner continues to build movie and TV caches every 15 minutes. The canonical application synchronizes stable scanner output into its application directories and reloads the indexes. Scanner files are base data only and are allowed to be replaced.

## Durable manual matches

Manual movie matches and uploaded artwork are no longer trusted to generated cache files. They are saved separately in:

- `media-download-library/manual-metadata-overrides.json`
- `media-download-library/manual-poster-overrides.json`

Whenever metadata or poster maps load, the generated scanner map loads first and the manual override map is applied second. Manual values therefore win after every scan and restart.

The correction for **The Long, Long Trailer** is stored as TMDB ID `24518`, release date `1954-02-19`, with poster `posters/tmdb-24518-w500.jpg`. A generated-cache replacement test confirmed the title, metadata, and poster still resolve from the override layer.

## Database backup

The nightly 1:00 AM database backup now targets the canonical database and backup directory:

```text
CINEVAULT_DB=/home/jnicolas/cinemediavault-lab/cinevault-data/cinemediavault-lab.db
CINEVAULT_BACKUP_DIR=/home/jnicolas/cinemediavault-lab/cinevault-data/backups
```

A verified SQLite backup was created immediately before promotion.

## Validation

```bash
curl -kfsS -o /dev/null -w '%{http_code}\n' https://127.0.0.1:5000/login
curl -kfsS -o /dev/null -w '%{http_code}\n' https://127.0.0.1:5000/api/homepage/status
ss -ltnp | grep ':5000'
! ss -ltn | grep -q ':8093 '
```

Expected results: HTTP 200 from both canonical endpoints, port 5000 listening, and no listener on port 8093.

## Rollback

Promotion created timestamped backups of:

- `/home/jnicolas/start_everything.sh`
- the user crontab
- `/home/jnicolas/cinemediavault-lab/media_download_server.py`
- `/home/jnicolas/homepage/services.yaml` on server 134
- the canonical SQLite database

To temporarily launch the retired application without changing the startup file:

```bash
CINEVAULT_LEGACY_ENABLED=1 /home/jnicolas/start_everything.sh
```

Do not run both applications as normal production services. Make changes only through the canonical port-5000 application so its durable override files remain authoritative.

## Partial HLS self-healing

CineVault validates cached HLS VOD playlists before reusing them. A playlist with `EXT-X-ENDLIST` is accepted only when the sum of its segment durations reaches the source duration within an eight-second/two-segment tolerance. If a restart terminates FFmpeg and leaves a shortened playlist that appears finished, CineVault now removes that stream directory and regenerates it on the next request.

This repair was validated against *I'm Sorry*, Season 1 Episode 4. The interrupted cache ended at 704.7 seconds while the source duration is 1,474.026 seconds; the validator rejected it and the episode-specific stale cache was removed.
