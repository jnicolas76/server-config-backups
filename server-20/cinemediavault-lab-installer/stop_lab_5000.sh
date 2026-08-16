#!/usr/bin/env bash
set -euo pipefail
LAB="/home/jnicolas/cinemediavault-lab"
PID_FILE="$LAB/cinemediavault-lab-5000.pid"
if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  kill "$(cat "$PID_FILE")" || true
  rm -f "$PID_FILE"
  echo "Stopped CineMediaVault LAB."
else
  echo "CineMediaVault LAB is not running."
fi
pgrep -af ffmpeg | awk 'index($0, "/tmp/cinemediavault-lab-hls") {print $1}' | xargs -r kill 2>/dev/null || true
