#!/usr/bin/env bash
set -euo pipefail

source_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
install_dir="${TRANSCODE_CONTROL_DIR:-$HOME/transcode-control}"
unit_dir="$HOME/.config/systemd/user"

test -f "$source_dir/controller.env" || {
  echo "Create $source_dir/controller.env from controller.env.example first." >&2
  exit 1
}
mkdir -p "$install_dir/static" "$install_dir/data" "$unit_dir"
install -m 0755 "$source_dir/server.py" "$install_dir/server.py"
install -m 0644 "$source_dir/static/index.html" "$install_dir/static/index.html"
install -m 0644 "$source_dir/static/app.js" "$install_dir/static/app.js"
install -m 0644 "$source_dir/static/advanced.css" "$install_dir/static/advanced.css"
install -m 0600 "$source_dir/controller.env" "$install_dir/controller.env"
cat > "$unit_dir/transcode-control.service" <<EOF
[Unit]
Description=CineMediaVault Transcode Control
After=network-online.target

[Service]
Type=simple
WorkingDirectory=$install_dir
EnvironmentFile=$install_dir/controller.env
ExecStart=/usr/bin/python3 $install_dir/server.py
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
EOF
python3 -m py_compile "$install_dir/server.py"
systemctl --user daemon-reload
systemctl --user enable --now transcode-control.service
echo "Controller installed at $install_dir"

