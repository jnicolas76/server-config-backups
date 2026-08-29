# Radarr/Sonarr download-remnant cleanup on server 134

`/home/jnicolas/scripts/arr-download-cleanup.py` is restricted to these two literal roots:

- `/home/jnicolas/downloads/radarr`
- `/home/jnicolas/downloads/tv-sonarr`

It evaluates only direct children of those roots. Adjacent download folders cannot be selected.

An entry is removed only when all of these checks pass:

1. The allowed root resolves to the expected literal path and is not a symlink.
2. The direct child is not a symlink and passes a parent-containment check.
3. The newest file anywhere inside it is older than one hour.
4. No qBittorrent `.fastresume` record references the entry.
5. Every video in the entry appears in Radarr or Sonarr successful-import history.
6. Each corresponding imported library file still exists on the mounted media share.

The default mode is dry-run. Deletion requires `--apply`. Each decision is emitted as one JSON line. The cron entry runs every 30 minutes under a non-blocking lock and sends output to the system journal under tag `arr-download-cleanup`.

Initial execution on 2026-08-29 removed 42 verified remnants and reclaimed 294,377,820,570 bytes. Four unverified entries were preserved. The initial dry-run and apply ledgers are stored in `/home/jnicolas/logs/`.
