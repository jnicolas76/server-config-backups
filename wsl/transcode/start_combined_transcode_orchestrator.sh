#!/usr/bin/env bash
set -euo pipefail

script=/mnt/c/DATA/combined_transcode_orchestrator.py
out=/mnt/c/DATA/combined-transcode-orchestrator-$(date +%Y%m%d).out
err=/mnt/c/DATA/combined-transcode-orchestrator-$(date +%Y%m%d).err
pidfile=/mnt/c/DATA/combined-transcode-orchestrator.pid

chmod +x "$script"
nohup python3 "$script" "$@" >"$out" 2>"$err" &
echo $! > "$pidfile"
echo "Started combined transcode orchestrator PID $(cat "$pidfile")"
echo "Output: $out"
echo "Errors: $err"
