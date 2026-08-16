#!/usr/bin/env bash
set -euo pipefail
STATE=/mnt/c/DATA/BRIDGETTE-H265
SCRIPT="$STATE/bridgette_h265_worker.py"
mkdir -p "$STATE"
rm -f "$STATE/watchdog.pause"
if pgrep -f "(^|/)python3 $SCRIPT" >/dev/null; then
  echo "Bridgette H.265 worker is already running."
  pgrep -af "(^|/)python3 $SCRIPT"
  exit 0
fi
nohup python3 "$SCRIPT" >> "$STATE/nohup.log" 2>&1 < /dev/null &
echo $! > "$STATE/worker.pid"
echo "Started Bridgette H.265 worker PID $!"
echo "Log: $STATE/bridgette-h265.log"
