#!/usr/bin/env bash
# promote_news.sh - atomically swaps the staged build into the live path the
# Cine News channel (T16) actually plays from. Refuses to promote a missing,
# tiny, or unprobeable file, so a failed/still-running render never blanks
# the channel - it just keeps airing whatever is already live.
set -euo pipefail
cd /home/jnicolas/cinevault-genchannel
STAGING=output/broadcast-staging.mp4
LIVE=output/broadcast-morning-v3.mp4
MIN_BYTES=1000000

if [[ ! -f "$STAGING" ]]; then
  echo "[$(date -Is)] no staged file at $STAGING - nothing to promote, leaving live file as-is." >&2
  exit 0
fi

size=$(stat -c%s "$STAGING" 2>/dev/null || echo 0)
if (( size < MIN_BYTES )); then
  echo "[$(date -Is)] staged file too small ($size bytes) - refusing to promote." >&2
  exit 1
fi

duration=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$STAGING" 2>/dev/null || echo 0)
if [[ -z "$duration" ]] || awk -v d="$duration" 'BEGIN{exit !(d<60)}'; then
  echo "[$(date -Is)] staged file failed duration check ($duration s) - refusing to promote." >&2
  exit 1
fi

mv -f "$STAGING" "$LIVE"
# Force T16's scheduler to re-probe the newly promoted edition and rebuild
# only its active/future mixed lineup. The app's five-minute scheduler owns
# database writes; this script never edits the production SQLite DB itself.
rm -f output/.cine-news-schedule-signature
echo "[$(date -Is)] promoted staging -> live (${duration}s). Now playing on Cine News."
