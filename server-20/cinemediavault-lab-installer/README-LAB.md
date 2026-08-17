# CineMediaVault Lab Instance

This is the isolated lab copy of production `8093`.

## Four-Video Wall

Open `/wall` after signing in, or use the **Video Wall** tab on the home page. Each user has four SQLite-backed slots that can mix movies and individual TV episodes. Search the library, assign an item to any slot, and remove or replace it later. Selecting a tile makes it the only audible source while the other videos keep playing at their current positions. Play-all, pause-all, restart, and sync controls are included.

## Paths

- Runtime: `/home/jnicolas/cinemediavault-lab`
- Installer scripts: `/home/jnicolas/cinemediavault-lab-installer`
- HTTPS URL: `https://192.168.1.20:5000/`
- Lab DB: `/home/jnicolas/cinemediavault-lab/cinevault-data/cinemediavault-lab.db`
- Lab HLS cache: `/tmp/cinemediavault-lab-hls`
- Lab logs: `/home/jnicolas/cinemediavault-lab/logs`

## Start / Stop

```bash
/home/jnicolas/cinemediavault-lab/start_lab_5000.sh
/home/jnicolas/cinemediavault-lab/stop_lab_5000.sh
```

## Refresh Lab From Production Code

This refreshes code/helper app folders from production but keeps the existing lab DB if it already exists:

```bash
/home/jnicolas/cinemediavault-lab-installer/install_or_refresh_lab_from_prod.sh
```

## HTTPS

The lab uses a self-signed TLS certificate generated in:

```text
/home/jnicolas/cinemediavault-lab/certs
```

Browsers will show a warning unless this certificate is trusted.

## Mobile Download Jobs

The lab includes compressed mobile downloads for movies, episodes, and seasons.
Admins can view live job status here:

```text
https://192.168.1.20:5000/admin/hls
```

That page shows HLS streams, direct streams, cached HLS folders, and mobile
download jobs with progress bars.

Mobile download cache is stored here:

```text
/home/jnicolas/cinemediavault-lab/mobile-download-cache
```

The cache self-cleans after the configured retention window and can also be
cleared from the admin page.

The lab installer prefers `cinemediavault-lab-5000.py.template` when present, so
refreshing the lab keeps lab-only features such as HTTPS, direct/HLS playback
mode, compressed mobile downloads, and admin progress status.

## Homepage / Status API

Production and lab expose a public, read-only Homepage status endpoint:

```text
http://192.168.1.20:8093/api/homepage/status
https://192.168.1.20:5000/api/homepage/status
```

The response includes movie, TV, stream, comic, book, and game-library counts.
Homepage uses the production `8093` endpoint through its `customapi` widget.

Important fields:

```text
movies
tv_shows
tv_episodes
books
comics
games
nes_games
sega_games
dos_games
mame_games
active_streams
modules
```

Book counts come from `/home/jnicolas/bookvault/book-index-cache.json` so the
status endpoint does not recursively walk the slower Books disk. Comic counts
come from `/media/jnicolas/Expansion/comic-library/collections`. Game counts come
from the module ROM roots under `/home/jnicolas/software/*/roms` plus arcade
ROMs under `/home/jnicolas/roms/arcade`.

## Lab mobile download size rule

Compressed mobile downloads in the lab target `MOBILE_DOWNLOAD_TARGET_SOURCE_RATIO=0.40`, meaning the HLS/compressed download path aims for about 40% of the original source size. Direct downloads remain original-size. `MOBILE_DOWNLOAD_MAX_OUTPUT_RATIO=0.98` prevents oversized compressed output from being offered.

## Four-video wall

Open `/wall` after signing in. Each user has four saved slots that can mix
movies and individual TV episodes. Every occupied tile includes independent
play/pause, 10-second rewind/forward, and timeline seeking controls. Selecting
a tile makes it the only audible source.

The `Bandwidth` button shows or hides a live meter. It reports actual bytes
served to that user's wall over a rolling three-second window, in Mbps and
bytes per second, plus the number of videos currently playing. The reading
falls automatically when playback is paused or a tile is removed.

Wall items are codec-checked when they are loaded. Browser-compatible video
and audio use direct Range playback. Sources such as HEVC/E-AC-3 automatically
use the existing HLS engine and are delivered as H.264/AAC, preventing silent
video or unsupported-codec failures. Probe results are cached by file size and
modification time. HLS segment traffic is included in the bandwidth meter.
