#!/usr/bin/env bash
set -euo pipefail

source_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
install_dir="${TRANSCODE_AGENT_DIR:-$HOME/.local/share/transcode-control-agent}"
unit_dir="$HOME/.config/systemd/user"

test -f "$source_dir/agent.env" || {
  echo "Create $source_dir/agent.env from agent.env.example first." >&2
  exit 1
}
mkdir -p "$install_dir" "$unit_dir"
install -m 0755 "$source_dir/agent.py" "$install_dir/agent.py"
install -m 0600 "$source_dir/agent.env" "$install_dir/agent.env"
cat > "$unit_dir/transcode-control-agent.service" <<EOF
[Unit]
Description=CineMediaVault Transcode Control agent
After=network-online.target

[Service]
Type=simple
WorkingDirectory=$install_dir
EnvironmentFile=$install_dir/agent.env
ExecStart=/usr/bin/python3 $install_dir/agent.py
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
EOF
python3 -m py_compile "$install_dir/agent.py"
systemctl --user daemon-reload
systemctl --user enable --now transcode-control-agent.service
echo "Agent installed at $install_dir"

