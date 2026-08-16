#!/usr/bin/env bash
set -euo pipefail

app_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
config="$app_dir/config/application.properties"

echo "Installing CineVault transcode orchestrator from: $app_dir"
echo "Config: $config"

if command -v apt-get >/dev/null 2>&1; then
  sudo apt-get update
  sudo apt-get install -y python3 python3-pip ffmpeg handbrake-cli cron
elif command -v dnf >/dev/null 2>&1; then
  sudo dnf install -y python3 python3-pip ffmpeg HandBrake-cli cronie
elif command -v yum >/dev/null 2>&1; then
  sudo yum install -y python3 python3-pip ffmpeg HandBrake-cli cronie
else
  echo "Could not detect apt/dnf/yum. Install python3, ffmpeg, HandBrakeCLI, and cron manually."
fi

chmod +x "$app_dir"/bin/*.sh "$app_dir"/bin/*.py
python3 -m py_compile "$app_dir"/bin/*.py

echo
echo "Install complete."
echo "Next:"
echo "1. Edit $config"
echo "2. Run: $app_dir/cron/install_transcode_cron.sh"
echo "3. Check: $app_dir/bin/status_transcode_orchestrator.sh"
