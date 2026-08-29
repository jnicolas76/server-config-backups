#!/usr/bin/env bash
set -euo pipefail

readonly REMOTE="jnicolas@192.168.1.20"
readonly DEST="/home/jnicolas/transcode/cinevault/home"

mkdir -p "$DEST/media-download-library" "$DEST/tv-download-library"

sync_one() {
  local source=$1
  local destination=$2
  local temporary="${destination}.tmp"
  scp -q -o BatchMode=yes -o ConnectTimeout=10 "$REMOTE:$source" "$temporary"
  test -s "$temporary"
  mv -f "$temporary" "$destination"
}

sync_one "/home/jnicolas/media-download-library/movie-live-index.json" \
  "$DEST/media-download-library/movie-live-index.json"
sync_one "/home/jnicolas/tv-download-library/tv-live-index.json" \
  "$DEST/tv-download-library/tv-live-index.json"

# Keep the bot-visible movie queue aligned with the freshly synced index.
# Reconciliation is atomic and never interrupts the active encode.
/usr/bin/python3 /home/jnicolas/transcode/reconcile_movie_queue.py
