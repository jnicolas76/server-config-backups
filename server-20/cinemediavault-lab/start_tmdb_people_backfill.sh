#!/usr/bin/env bash
set -euo pipefail
BASE=/home/jnicolas/cinemediavault-lab
LOG="$BASE/logs/tmdb-people-backfill.log"
PIDFILE="$BASE/.tmdb-people-backfill.pid"
mkdir -p "$BASE/logs" "$BASE/cinevault-data/tmdb-people"
if [[ -s "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "Backfill already running PID $(cat "$PIDFILE")"
  exit 0
fi
nohup /usr/bin/flock -n /tmp/cinevault-tmdb-people.lock \
  /usr/bin/nice -n 15 /usr/bin/ionice -c3 /usr/bin/python3 \
  "$BASE/cache_tmdb_people.py" --media-limit 0 --cast-limit 20 --sleep 0.25 \
  >>"$LOG" 2>&1 </dev/null &
echo $! >"$PIDFILE"
echo "Started TMDB people backfill PID $(cat "$PIDFILE"); log=$LOG"
