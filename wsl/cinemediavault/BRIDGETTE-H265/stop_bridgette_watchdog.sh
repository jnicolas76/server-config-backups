#!/usr/bin/env bash
set -u

ROOT=/mnt/c/DATA/BRIDGETTE-H265
WATCHDOG="$ROOT/bridgette_h265_watchdog.py"

touch "$ROOT/watchdog.pause"
if pgrep -f "python3 $WATCHDOG" >/dev/null 2>&1; then
    pkill -TERM -f "python3 $WATCHDOG"
    echo "Stopped Bridgette H265 watchdog and paused automatic recovery."
else
    echo "Bridgette H265 watchdog is not running; automatic recovery is paused."
fi
