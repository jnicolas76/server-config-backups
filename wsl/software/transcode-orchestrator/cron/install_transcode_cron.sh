#!/usr/bin/env bash
set -euo pipefail

app_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
config="${TRANSCODE_ORCHESTRATOR_CONFIG:-$app_dir/config/application.properties}"

prop() {
  local key="$1"
  local default="$2"
  local value=""
  if [[ -f "$config" ]]; then
    value="$(awk -F= -v k="$key" 'BEGIN{v=""} /^[[:space:]]*[#;]/ {next} $1 ~ "^[[:space:]]*"k"[[:space:]]*$" {sub(/^[^=]*=/,""); gsub(/^[[:space:]]+|[[:space:]]+$/,""); v=$0} END{print v}' "$config")"
  fi
  printf '%s' "${value:-$default}"
}

minute="$(prop watchdog.cron.minute 7)"
watchdog="$app_dir/bin/transcode_queue_watchdog.py"
data_dir="$(prop data.dir /mnt/c/DATA)"
cron_log="$data_dir/transcode-queue-watchdog.cron.log"

tmp="$(mktemp)"
crontab -l 2>/dev/null | grep -v 'transcode_queue_watchdog.py' > "$tmp" || true
{
  cat "$tmp"
  echo "@reboot /usr/bin/python3 '$watchdog' >> '$cron_log' 2>&1"
  echo "$minute * * * * /usr/bin/python3 '$watchdog' >> '$cron_log' 2>&1"
} | crontab -
rm -f "$tmp"

echo "Installed transcode watchdog cron:"
crontab -l | grep 'transcode_queue_watchdog.py'
