#!/usr/bin/env bash
#
# CineMediaVault clean-VM smoke test.
#
# Run this on a freshly installed machine to confirm the installation actually
# works. It observes real behaviour - ports answering, database rows existing,
# timers scheduled - rather than checking that files were written.
#
# It is safe to run at any time on a live server: nothing is modified, no tuner
# is claimed, and no library scan is started.
#
#   sudo ./tools/smoke-test.sh              the standard checks
#   sudo ./tools/smoke-test.sh --deep       also probe ffmpeg and the tuner
#   sudo ./tools/smoke-test.sh --json       machine-readable output
#
set -euo pipefail

CONFIG="${CMV_CONFIG:-/etc/cinemediavault/cinemediavault.yaml}"
ARGS=()
JSON=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --deep)     ARGS+=(--deep); shift ;;
    --json)     JSON=1; shift ;;
    --config)   CONFIG="${2:-}"; shift 2 ;;
    --config=*) CONFIG="${1#*=}"; shift ;;
    --help|-h)
      sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) printf 'unknown option: %s\n' "$1" >&2; exit 2 ;;
  esac
done

if [[ ! -r "$CONFIG" ]]; then
  printf 'No CineMediaVault configuration at %s.\n' "$CONFIG" >&2
  printf 'Is it installed? Try: sudo cinevaultctl status\n' >&2
  exit 2
fi

if command -v cinevaultctl >/dev/null 2>&1; then
  RUNNER=(cinevaultctl)
else
  ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." >/dev/null 2>&1 && pwd -P)"
  RUNNER=(python3 -m installer)
  cd "$ROOT"
fi

CMD=("${RUNNER[@]}" --config "$CONFIG")
(( JSON )) && CMD+=(--json)
CMD+=(smoke-test)
[[ ${#ARGS[@]} -gt 0 ]] && CMD+=("${ARGS[@]}")

exec "${CMD[@]}"
