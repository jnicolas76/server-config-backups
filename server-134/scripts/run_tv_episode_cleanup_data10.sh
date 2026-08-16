#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="/home/jnicolas/Data10/TV_Shows"
plan="$HOME/tv-episode-filename-plan-data10.csv"
log="$HOME/tv-episode-filename-changes-data10.csv"

if [[ ! -d "$root" ]]; then
  echo "TV folder not found: $root" >&2
  exit 1
fi
if [[ ! -f "$script_dir/normalize_tv_episode_files.py" ]]; then
  echo "Missing: $script_dir/normalize_tv_episode_files.py" >&2
  exit 1
fi

exec ionice -c3 nice -n 19 python3 \
  "$script_dir/normalize_tv_episode_files.py" \
  --root "$root" \
  --plan "$plan" \
  --log "$log" \
  --apply \
  --delay 0.10 \
  --scan-delay 0.002 \
  --progress-every 250
