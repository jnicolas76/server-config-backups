#!/usr/bin/env bash
set -euo pipefail

ROLE="${1:-}"
case "$ROLE" in
  server)
    install_dir="${INSTALL_DIR:-$HOME/transcode-control}"
    mkdir -p "$install_dir" "$HOME/.config/systemd/user"
    cp -a server.py static README.md server.env.example transcode-control.service "$install_dir/"
    cp transcode-control.service "$HOME/.config/systemd/user/"
    if [[ ! -f "$install_dir/server.env" ]]; then
      cp "$install_dir/server.env.example" "$install_dir/server.env"
      echo "Edit $install_dir/server.env before starting."
    fi
    systemctl --user daemon-reload
    systemctl --user enable transcode-control.service
    ;;
  agent)
    install_dir="${INSTALL_DIR:-/mnt/c/DATA/transcode-control-agent}"
    mkdir -p "$install_dir" "$HOME/.config/systemd/user"
    cp agent.py agent.env.example transcode-control-agent.service "$install_dir/"
    cp transcode-control-agent.service "$HOME/.config/systemd/user/"
    if [[ ! -f "$install_dir/agent.env" ]]; then
      cp "$install_dir/agent.env.example" "$install_dir/agent.env"
      echo "Edit $install_dir/agent.env before starting."
    fi
    systemctl --user daemon-reload
    systemctl --user enable transcode-control-agent.service
    ;;
  *) echo "Usage: $0 server|agent" >&2; exit 2 ;;
esac

