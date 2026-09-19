#!/usr/bin/env bash
set -euo pipefail
BASE="/home/jnicolas/cinemediavault-lab"
APP="$BASE/cine-guide-magazine"
OUT="$APP/weekly-guides"
PY="$APP/.venv/bin/python"
mkdir -p "$OUT" "$APP/logs"
if [[ ! -x "$PY" ]]; then
  python3 -m venv "$APP/.venv"
  "$APP/.venv/bin/pip" install --disable-pip-version-check reportlab Pillow pypdf pdfplumber
fi
WEEK="$(date -d 'monday this week' +%F)"
STAMP="$(date -d "$WEEK" +%Y-%m-%d)"
exec flock -n /tmp/cine-guide-magazine.lock "$PY" "$APP/generate_weekly_guide.py" \
  --base "$BASE" --db "$BASE/cinevault-data/cinemediavault-lab.db" \
  --week "$WEEK" --output "$OUT/CineMedia-Vault-Guide-$STAMP.pdf"
