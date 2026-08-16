#!/usr/bin/env bash
set -euo pipefail
STATE=/mnt/c/DATA/BRIDGETTE-H265
SCRIPT="$STATE/bridgette_h265_worker.py"
touch "$STATE/watchdog.pause"
if pgrep -f "(^|/)python3 $SCRIPT" >/dev/null; then
  pkill -TERM -f "(^|/)python3 $SCRIPT"
  echo "Stop requested; the worker will exit after its current ffmpeg process finishes. Automatic restart is paused."
else
  echo "Bridgette H.265 worker is not running. Automatic restart is paused."
fi
