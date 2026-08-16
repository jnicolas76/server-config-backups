#!/usr/bin/env bash
set -euo pipefail
crontab -l 2>/dev/null | grep -E 'transcode|handbrake|webex|orchestrator|watchdog' || true
