#!/usr/bin/env bash
set -euo pipefail

app_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
target="${1:-$HOME/bin}"
mkdir -p "$target" "$HOME/.config/webex"
chmod +x "$app_dir/bin/send_webex_notification" "$app_dir/bin/send_webex_notification.py"
[[ -f "$app_dir/bin/webex_bot_listener.py" ]] && chmod +x "$app_dir/bin/webex_bot_listener.py"
ln -sf "$app_dir/bin/send_webex_notification" "$target/send_webex_notification"
[[ -f "$app_dir/bin/webex_bot_listener.py" ]] && ln -sf "$app_dir/bin/webex_bot_listener.py" "$target/webex_bot_listener.py"

if [[ ! -f "$HOME/.config/webex/token" ]]; then
  cat > "$HOME/.config/webex/token" <<'TOKEN'
PUT_WEBEX_BOT_OR_PERSONAL_ACCESS_TOKEN_HERE
TOKEN
  chmod 600 "$HOME/.config/webex/token"
fi

echo "Installed WebEx notifier symlink: $target/send_webex_notification"
[[ -f "$app_dir/bin/webex_bot_listener.py" ]] && echo "Installed WebEx listener symlink: $target/webex_bot_listener.py"
echo "Edit token file: $HOME/.config/webex/token"
echo "Edit settings: $app_dir/config/application.properties"
