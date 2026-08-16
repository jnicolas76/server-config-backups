#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
PID_FILE=".tmdb-poster-fetch.pid"
OUT="logs/tmdb-poster-fetch-$(date +%Y%m%d-%H%M%S).out"
ERR="logs/tmdb-poster-fetch-$(date +%Y%m%d-%H%M%S).err"

if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "TMDb poster fetch is already running as PID $(cat "$PID_FILE")."
  exit 0
fi

mkdir -p logs posters
nohup python3 fetch_tmdb_posters.py --limit 0 --sleep 0.05 >"$OUT" 2>"$ERR" &
echo $! > "$PID_FILE"
echo "Started TMDb poster fetch PID $(cat "$PID_FILE")."
echo "Output: $OUT"
echo "Errors: $ERR"
