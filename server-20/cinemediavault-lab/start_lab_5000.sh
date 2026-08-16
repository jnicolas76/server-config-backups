#!/usr/bin/env bash
set -euo pipefail
LAB="/home/jnicolas/cinemediavault-lab"
set -a
source "$LAB/cinemediavault-lab.env"
set +a
cd "$LAB"
PID_FILE="$LAB/cinemediavault-lab-5000.pid"
LOG_FILE="$LAB/logs/cinemediavault-lab-5000.log"
ERR_FILE="$LAB/logs/cinemediavault-lab-5000.err"
PORT="${CINEVAULT_PORT:-5000}"
HEALTH_URL="https://127.0.0.1:${PORT}/login"

healthy() {
  curl -k -fsS --connect-timeout 2 --max-time 5 "$HEALTH_URL" >/dev/null 2>&1
}

stop_stale() {
  if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    kill "$(cat "$PID_FILE")" 2>/dev/null || true
    sleep 2
  fi
  pids=$(pgrep -f '^python3 /home/jnicolas/cinemediavault-lab/cinemediavault-lab-5000.py' || true)
  if [[ -n "$pids" ]]; then
    printf '%s
' $pids | xargs -r kill 2>/dev/null || true
    sleep 2
  fi
  pids=$(pgrep -f '^python3 /home/jnicolas/cinemediavault-lab/cinemediavault-lab-5000.py' || true)
  if [[ -n "$pids" ]]; then
    printf '%s
' $pids | xargs -r kill -9 2>/dev/null || true
  fi
  rm -f "$PID_FILE"
}

if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  if healthy; then
    echo "CineMediaVault LAB already running PID $(cat "$PID_FILE")"
    echo "URL: https://192.168.1.20:${PORT}/"
    exit 0
  fi
  echo "CineMediaVault LAB PID $(cat "$PID_FILE") is unresponsive; restarting."
  stop_stale
fi

mkdir -p "$LAB/logs"
nohup python3 "$LAB/cinemediavault-lab-5000.py" --host "$CINEVAULT_HOST" --port "$PORT" >"$LOG_FILE" 2>"$ERR_FILE" &
echo $! > "$PID_FILE"
sleep 2
if kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  if healthy; then
    echo "Started CineMediaVault LAB PID $(cat "$PID_FILE")"
    echo "URL: https://192.168.1.20:${PORT}/"
  else
    echo "LAB process started but health check failed. Check $ERR_FILE" >&2
    exit 1
  fi
else
  echo "LAB failed to start. Check $ERR_FILE" >&2
  exit 1
fi
