#!/usr/bin/env bash
set -euo pipefail

cd /mnt/c/DATA/bridgette-vault
PID_FILE=".bridgette-vault.pid"

if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  kill "$(cat "$PID_FILE")"
  rm -f "$PID_FILE"
  echo "Stopped Private Vault."
else
  echo "Private Vault is not running."
fi
