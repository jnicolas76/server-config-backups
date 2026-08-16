#!/usr/bin/env bash
set -euo pipefail

DATA_DIR="/mnt/c/DATA"
PID_FILE="$DATA_DIR/webex-bot-listener.pid"
LOG_FILE="$DATA_DIR/webex-bot-listener.log"
SCRIPT="$DATA_DIR/webex_bot_listener.py"

if [[ -f "$PID_FILE" ]]; then
  old_pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "${old_pid:-}" ]] && kill -0 "$old_pid" 2>/dev/null; then
    echo "WebEx bot listener already running: PID $old_pid"
    exit 0
  fi
fi

nohup /usr/bin/python3 "$SCRIPT" >> "$LOG_FILE" 2>&1 &
echo "$!" > "$PID_FILE"
echo "Started WebEx bot listener: PID $(cat "$PID_FILE")"
