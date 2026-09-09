#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
TARGET_DIR="${1:-/home/jnicolas/cinemediavault-lab}"
STAMP="$(date +%Y%m%d-%H%M%S)"

mkdir -p "$TARGET_DIR" "$TARGET_DIR/backups/install-$STAMP"

files=(
  cinemediavault-lab-5000.py
  virtual_channels.py
  genre_catalog.py
  cinevault_theme.py
  cinevault_usage.py
  cinevault_video_lists.py
  epg_extend.py
  dvr_module.py
  music_module.py
  media_download_server.py
  tv_download_server.py
  test_virtual_channels.py
  repair_virtual_schedules.py
  start_lab_5000.sh
  stop_lab_5000.sh
)

for name in "${files[@]}"; do
  [[ -f "$SOURCE_DIR/$name" ]] || continue
  if [[ -f "$TARGET_DIR/$name" ]]; then
    cp -a "$TARGET_DIR/$name" "$TARGET_DIR/backups/install-$STAMP/$name"
  fi
  install -m 0644 "$SOURCE_DIR/$name" "$TARGET_DIR/$name"
done

chmod +x "$TARGET_DIR/start_lab_5000.sh" "$TARGET_DIR/stop_lab_5000.sh" "$TARGET_DIR/repair_virtual_schedules.py" 2>/dev/null || true
python3 -m py_compile "$TARGET_DIR/cinemediavault-lab-5000.py" "$TARGET_DIR/virtual_channels.py"

printf 'CineMediaVault source installed in %s\n' "$TARGET_DIR"
printf 'Previous files backed up in %s/backups/install-%s\n' "$TARGET_DIR" "$STAMP"
printf 'Configuration, TLS, database, media mounts, EPG and credentials remain destination-specific.\n'
printf 'Start with: %s/start_lab_5000.sh\n' "$TARGET_DIR"
