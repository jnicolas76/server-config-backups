#!/usr/bin/env bash
set -euo pipefail
crontab -l 2>/dev/null | grep -E 'cinevault|transcode|handbrake|webex|orchestrator|watchdog|movie|tv' || true
