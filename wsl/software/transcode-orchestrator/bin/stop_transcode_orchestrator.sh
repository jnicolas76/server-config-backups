#!/usr/bin/env bash
set -euo pipefail

echo "Stopping orchestrator and active HandBrake workers..."
pkill -f 'combined_transcode_orchestrator.py' 2>/dev/null || true
pkill -f 'handbrake_transcode_worker.py' 2>/dev/null || true
pkill -f 'HandBrakeCLI' 2>/dev/null || true
echo "Stopped matching processes."
