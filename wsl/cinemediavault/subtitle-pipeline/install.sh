#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
install_root="$HOME/.local/share/cinevault-subtitles"
bin_dir="$HOME/.local/bin"
unit_dir="$HOME/.config/systemd/user"
mkdir -p "$install_root" "$bin_dir" "$unit_dir" "$HOME/.local/state/cinevault-subtitles" "$HOME/.cache/cinevault-subtitles"
cp "$project_dir/cinevault_subtitles.py" "$project_dir/audio_events_to_srt.py" "$project_dir/config.json" "$install_root/"
python3 -m venv "$install_root/venv"
"$install_root/venv/bin/pip" install --upgrade pip wheel
"$install_root/venv/bin/pip" install faster-whisper argostranslate torch transformers soundfile
"$install_root/venv/bin/pip" install nvidia-cublas-cu12 nvidia-cudnn-cu12
ln -sfn "$project_dir/bin/subtitle-pipeline" "$bin_dir/subtitle-pipeline"
ln -sfn "$project_dir/bin/install-translation-models" "$bin_dir/install-translation-models"
cp "$project_dir/systemd/"* "$unit_dir/"
systemctl --user daemon-reload
systemctl --user enable --now cinevault-subtitle-scan.timer
echo "Installed. Run: subtitle-pipeline scan"
