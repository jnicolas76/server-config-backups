#!/usr/bin/env bash
set -euo pipefail

log="/mnt/c/DATA/PIP/copa-commercial-transcode.log"
printf '\n[%s] Starting Copa commercial-removal queue\n' "$(date '+%F %T')" >>"$log"
exec python3 /mnt/c/DATA/copa-commercial-transcode.py "$@" >>"$log" 2>&1
