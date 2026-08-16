#!/usr/bin/env bash
set -euo pipefail

echo "Stopping WSL transcode queue processes..."
pkill -f 'combined_transcode_orchestrator.py' || true
pkill -f 'handbrake_transcode_worker.py' || true
pkill -f 'HandBrakeCLI' || true
pkill -f 'ffmpeg.*mobile-download|ffmpeg.*HANDBRAKE|ffmpeg.*transcode' || true
rm -f /mnt/c/DATA/combined-transcode-orchestrator.pid
echo "Remaining matching processes:"
ps -eo pid,stat,etime,cmd | grep -Ei 'combined_transcode_orchestrator|handbrake_transcode_worker|HandBrakeCLI|ffmpeg.*(mobile-download|HANDBRAKE|transcode)' | grep -v grep || true
