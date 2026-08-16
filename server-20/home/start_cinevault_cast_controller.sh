#!/usr/bin/env bash
set -euo pipefail
cd /home/jnicolas
pid_file="/home/jnicolas/cinevault-cast-controller.pid"
log_file="/home/jnicolas/cinevault-cast-controller.log"
if [[ -f "$pid_file" ]]; then
  pid="$(cat "$pid_file" 2>/dev/null || true)"
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    echo "CineVault cast controller already running PID $pid"
    exit 0
  fi
fi
nohup /home/jnicolas/.cinevault-cast-venv/bin/python /home/jnicolas/cinevault_cast_controller.py >> "$log_file" 2>&1 &
echo $! > "$pid_file"
echo "Started CineVault cast controller PID $(cat "$pid_file")"
echo "URL: http://localhost:8120/"
