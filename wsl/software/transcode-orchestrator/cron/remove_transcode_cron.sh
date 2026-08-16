#!/usr/bin/env bash
set -euo pipefail

tmp="$(mktemp)"
crontab -l 2>/dev/null | grep -v 'transcode_queue_watchdog.py' > "$tmp" || true
crontab "$tmp"
rm -f "$tmp"
echo "Removed transcode watchdog cron entries."
