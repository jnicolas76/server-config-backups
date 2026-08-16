#!/usr/bin/env bash
set -euo pipefail

script=/mnt/c/DATA/combined_transcode_orchestrator.py
movie_out=/mnt/c/DATA/movie-lane-transcode-$(date +%Y%m%d).out
movie_err=/mnt/c/DATA/movie-lane-transcode-$(date +%Y%m%d).err
tv_out=/mnt/c/DATA/tv-avi-lane-transcode-$(date +%Y%m%d).out
tv_err=/mnt/c/DATA/tv-avi-lane-transcode-$(date +%Y%m%d).err
movie_pidfile=/mnt/c/DATA/movie-lane-transcode.pid
tv_pidfile=/mnt/c/DATA/tv-avi-lane-transcode.pid

chmod +x "$script"

is_running() {
  local pidfile="$1"
  [[ -s "$pidfile" ]] || return 1
  local pid
  pid="$(cat "$pidfile" 2>/dev/null || true)"
  [[ -n "$pid" && -d "/proc/$pid" ]] || return 1
  tr '\0' ' ' <"/proc/$pid/cmdline" | grep -q 'combined_transcode_orchestrator.py'
}

if ! is_running "$movie_pidfile"; then
  nohup python3 "$script" --skip-copa --only-movie --ignore-active --movie-target-gb 1.0 >"$movie_out" 2>"$movie_err" &
  echo $! > "$movie_pidfile"
  echo "Started movie lane PID $(cat "$movie_pidfile")"
else
  echo "Movie lane already running PID $(cat "$movie_pidfile")"
fi

if ! is_running "$tv_pidfile"; then
  nohup python3 "$script" --skip-copa --only-tv --ignore-active --tv-target-gb 0.7 >"$tv_out" 2>"$tv_err" &
  echo $! > "$tv_pidfile"
  echo "Started TV AVI lane PID $(cat "$tv_pidfile")"
else
  echo "TV AVI lane already running PID $(cat "$tv_pidfile")"
fi

echo "Movie output: $movie_out"
echo "Movie errors: $movie_err"
echo "TV AVI output: $tv_out"
echo "TV AVI errors: $tv_err"
