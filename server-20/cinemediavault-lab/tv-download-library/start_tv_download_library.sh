#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
PORT="${1:-8094}"
HOST="${HOST:-0.0.0.0}"
PID_FILE=".tv-download-library.pid"

if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "TV download library is already running as PID $(cat "$PID_FILE")."
  echo "URL: http://localhost:${PORT}/"
  exit 0
fi

mkdir -p logs posters
export TV_ROOT="${TV_ROOT:-/home/jnicolas/Data2/TV Shows}"
export TV_LIVE_CACHE="${TV_LIVE_CACHE:-$PWD/tv-live-index.json}"
export TV_POSTER_MAP="${TV_POSTER_MAP:-$PWD/tv-poster-map.json}"
export TV_POSTER_DIR="${TV_POSTER_DIR:-$PWD/posters}"
export TV_METADATA_MAP="${TV_METADATA_MAP:-$PWD/tv-metadata-map.json}"
nohup python3 tv_download_server.py --host "$HOST" --port "$PORT" > logs/server.log 2> logs/server.err &
echo $! > "$PID_FILE"
sleep 1

if kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "Started TV download library PID $(cat "$PID_FILE")."
  echo "URL on this PC: http://localhost:${PORT}/"
else
  echo "Server failed to start. Check logs/server.err"
  exit 1
fi
