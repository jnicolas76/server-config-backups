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
script="$(prop orchestrator.script "$app_dir/bin/combined_transcode_orchestrator.py")"
pidfile="$(prop orchestrator.pid.file "$data_dir/combined-transcode-orchestrator.pid")"
out="$data_dir/combined-transcode-orchestrator-$(date +%Y%m%d).out"
err="$data_dir/combined-transcode-orchestrator-$(date +%Y%m%d).err"

mkdir -p "$data_dir"
chmod +x "$script"
nohup python3 "$script" --config "$config" "$@" >"$out" 2>"$err" &
echo $! > "$pidfile"
echo "Started combined transcode orchestrator PID $(cat "$pidfile")"
echo "Output: $out"
echo "Errors: $err"
