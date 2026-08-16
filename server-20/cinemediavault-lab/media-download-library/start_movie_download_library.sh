#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
PORT="${1:-8120}"
HOST="${HOST:-0.0.0.0}"
PID_FILE=".movie-download-library.pid"

if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "Movie download library is already running as PID $(cat "$PID_FILE")."
  echo "URL: http://localhost:${PORT}/"
  exit 0
fi

mkdir -p logs
export MOVIE_ROOT="${MOVIE_ROOT:-/home/jnicolas/Data4/Movies}"
export MOVIE_LIVE_CACHE="${MOVIE_LIVE_CACHE:-$PWD/movie-live-index.json}"
export MOVIE_POSTER_MAP="${MOVIE_POSTER_MAP:-$PWD/poster-map.json}"
export MOVIE_POSTER_DIR="${MOVIE_POSTER_DIR:-$PWD/posters}"
export MOVIE_METADATA_MAP="${MOVIE_METADATA_MAP:-$PWD/movie-metadata-map.json}"
nohup python3 media_download_server.py --host "$HOST" --port "$PORT" > logs/server.log 2> logs/server.err &
echo $! > "$PID_FILE"
sleep 1

if kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "Started movie download library PID $(cat "$PID_FILE")."
  echo "URL on this PC: http://localhost:${PORT}/"
else
  echo "Server failed to start. Check logs/server.err"
  exit 1
fi
