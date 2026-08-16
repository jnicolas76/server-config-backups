#!/usr/bin/env bash
set -u

ROOT=/mnt/c/DATA/BRIDGETTE-H265
WATCHDOG="$ROOT/bridgette_h265_watchdog.py"
LOG="$ROOT/watchdog-launch.log"

mkdir -p "$ROOT"
rm -f "$ROOT/watchdog.pause"

if pgrep -f "python3 $WATCHDOG" >/dev/null 2>&1; then
    echo "Bridgette H265 watchdog is already running."
    exit 0
fi

nohup python3 "$WATCHDOG" >>"$LOG" 2>&1 </dev/null &
echo "Started Bridgette H265 watchdog (PID $!)."
