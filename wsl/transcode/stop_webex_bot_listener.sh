#!/usr/bin/env bash
set -euo pipefail

DATA_DIR="/mnt/c/DATA"
PID_FILE="$DATA_DIR/webex-bot-listener.pid"

if [[ -f "$PID_FILE" ]]; then
  pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "${pid:-}" ]] && kill -0 "$pid" 2>/dev/null; then
    kill "$pid" || true
    sleep 2
    if kill -0 "$pid" 2>/dev/null; then
      kill -9 "$pid" || true
    fi
    echo "Stopped WebEx bot listener PID $pid"
  else
    echo "No running listener found from pid file"
  fi
  rm -f "$PID_FILE"
else
  pkill -f "/mnt/c/DATA/webex_bot_listener.py" && echo "Stopped matching listener process" || echo "No listener process found"
fi
