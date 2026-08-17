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
pids=$(pgrep -f '^python3 /home/jnicolas/cinemediavault-lab/cinemediavault-lab-5000.py' || true)
if [[ -n "$pids" ]]; then
  printf '%s\n' $pids | xargs -r kill 2>/dev/null || true
  sleep 2
  pids=$(pgrep -f '^python3 /home/jnicolas/cinemediavault-lab/cinemediavault-lab-5000.py' || true)
  [[ -z "$pids" ]] || printf '%s\n' $pids | xargs -r kill -9 2>/dev/null || true
fi
rm -f "$PID_FILE"
pgrep -af ffmpeg | awk 'index($0, "/tmp/cinemediavault-lab-hls") {print $1}' | xargs -r kill 2>/dev/null || true
