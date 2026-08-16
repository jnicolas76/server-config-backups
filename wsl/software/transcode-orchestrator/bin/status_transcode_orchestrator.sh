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

data_dir="$(prop data.dir /mnt/c/DATA)"
pidfile="$(prop orchestrator.pid.file "$data_dir/combined-transcode-orchestrator.pid")"
movie_log="$(prop movies.log "$data_dir/combined-transcode-movies-h265.log")"
tv_log="$(prop tv.log "$data_dir/combined-transcode-tv-h265.log")"

echo "Transcode processes:"
pgrep -af 'combined_transcode|handbrake_transcode_worker|HandBrakeCLI' || true
echo
echo "PID file: $pidfile"
[[ -f "$pidfile" ]] && cat "$pidfile" || true
echo
echo "Latest movie progress:"
tail -n 250 "$movie_log" 2>/dev/null | grep 'Encoding:' | tail -n 1 || true
echo
echo "Latest TV progress:"
tail -n 250 "$tv_log" 2>/dev/null | grep 'Encoding:' | tail -n 1 || true
