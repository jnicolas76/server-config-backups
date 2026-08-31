# CineVault Stable Movie Match Repair — 2026-08-31

## Outcome

CineVault on `192.168.1.20:5000` now keeps manually selected movie metadata and
posters through catalog refreshes, video transcodes, extension changes, and quality
suffix changes.

## Root cause

The canonical app loaded the movie metadata/poster maps into memory at startup.
The scheduled library refresh atomically replaced those maps afterward, but the
running app did not notice the replacement. A correct map could therefore exist on
disk while the UI continued showing stale `No Poster` data.

Legacy Fix Match overrides were also keyed only by the exact relative video path.
Changing a file from `.mkv` to `.mp4`, or changing its quality suffix, orphaned the
override even when the movie folder remained unchanged.

## Permanent behavior

- The movie module checks the four automatic/manual map files for changes and
  reloads them without restarting CineVault.
- Manual metadata and posters take precedence over generated data.
- Fix Match and custom artwork now write both the legacy exact-path key and a stable
  `asset:<normalized-folder>` key.
- Existing exact-path manual overrides are migrated to stable keys at startup.
- Stable keys are based on the movie folder, so they survive file extension and
  encode-name changes while retaining the title/year identity used by the folder.

## Repaired assets

- `Home on the Range (2004)` is pinned to TMDb `13700`, with poster
  `posters/tmdb-13700-w342.jpg`.
- `Motor City (2026)` is pinned to TMDb `87513`, with poster
  `posters/tmdb-87513-w342.jpg`.

## Live paths

- App wrapper: `/home/jnicolas/cinemediavault-lab/cinemediavault-lab-5000.py`
- Movie module: `/home/jnicolas/cinemediavault-lab/media-download-library/media_download_server.py`
- Automatic metadata: `movie-metadata-map.json`
- Automatic posters: `poster-map.json`
- Durable metadata overrides: `manual-metadata-overrides.json`
- Durable poster overrides: `manual-poster-overrides.json`

The scheduled refresh does not write the two manual override files.

## Verification

The repaired module and canonical wrapper compiled successfully. CineVault's final
restart used PID `1386493`,
the HTTPS login health check returned `200`, and direct module verification resolved:

- Home on the Range → stable key `asset:homeontherange2004`, TMDb `13700`
- Motor City → stable key `asset:motorcity2026`, TMDb `87513`

The canonical wrapper invokes the legacy-manual-override migration during both
startup and media-state reloads. Its first verified startup migrated 10 legacy
manual overrides to stable identities.

## Recovery backups on server .20

- Pre-change: `/home/jnicolas/cinemediavault-lab/backups/stable-movie-matches-pre-20260831-114000`
- Post-change: `/home/jnicolas/cinemediavault-lab/backups/stable-movie-matches-post-20260831-114500`

Each backup contains the movie module and all four movie map/override files.
