#!/usr/bin/env bash
set -euo pipefail

suite_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Installing CineVault Linux suite from: $suite_dir"
"$suite_dir/webex-notifier/bin/install_webex_notifier.sh"
"$suite_dir/transcode-orchestrator/install.sh"

echo
echo "Optional next step:"
echo "$suite_dir/transcode-orchestrator/cron/install_transcode_cron.sh"
echo
echo "Edit configs before enabling cron:"
echo "$suite_dir/webex-notifier/config/application.properties"
echo "$suite_dir/transcode-orchestrator/config/application.properties"
