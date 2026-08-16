#!/usr/bin/env bash
set -euo pipefail

cd /mnt/c/DATA/bridgette-vault
PORT="${1:-8097}"
HOST="${HOST:-0.0.0.0}"
PID_FILE=".bridgette-vault.pid"

if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "Private Vault is already running as PID $(cat "$PID_FILE")."
  echo "URL: http://localhost:${PORT}/"
  exit 0
fi

mkdir -p logs
export BRIDGETTE_MEDIA_ROOT="${BRIDGETTE_MEDIA_ROOT:-/mnt/d/BRIDGE/Bridgette B - MegaPack}"
export BRIDGETTE_PASSWORD="${BRIDGETTE_PASSWORD:-CHANGE_ME}"
export BRIDGETTE_SESSION_SECRET="${BRIDGETTE_SESSION_SECRET:-CHANGE_ME}"

nohup python3 bridgette_vault_server.py --host "$HOST" --port "$PORT" > logs/server.log 2> logs/server.err &
echo $! > "$PID_FILE"
sleep 1

if kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "Started Private Vault PID $(cat "$PID_FILE")."
  echo "URL: http://localhost:${PORT}/"
else
  echo "Private Vault failed to start. Check logs/server.err"
  exit 1
fi
