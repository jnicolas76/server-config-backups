#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# CineMediaVault lab install/refresh helper.
# This creates or refreshes the isolated HTTPS lab instance on port 5000.
# It intentionally keeps lab DB/cache/logs separate from production 8093.

LAB="${LAB:-/home/jnicolas/cinemediavault-lab}"
PROD_HOME="${PROD_HOME:-/home/jnicolas}"
PORT="${PORT:-5000}"
HOST="${HOST:-0.0.0.0}"
mkdir -p "$LAB" "$LAB/logs" "$LAB/certs" "$LAB/cinevault-data" "$LAB/media-library-logs" "$LAB/media-library-refresh-logs" "$LAB/media-library-assets"

LAB_TEMPLATE="$SCRIPT_DIR/cinemediavault-lab-5000.py.template"
if [[ -f "$LAB_TEMPLATE" ]]; then
  cp -f "$LAB_TEMPLATE" "$LAB/cinemediavault-lab-5000.py"
else
  cp -f "$PROD_HOME/cinevault-watch-8093.py" "$LAB/cinemediavault-lab-5000.py"
fi
rsync -a --delete "$PROD_HOME/media-download-library/" "$LAB/media-download-library/"
rsync -a --delete "$PROD_HOME/tv-download-library/" "$LAB/tv-download-library/"
rsync -a "$PROD_HOME/media-library-assets/" "$LAB/media-library-assets/" 2>/dev/null || true
if [[ ! -f "$LAB/cinevault-data/cinemediavault-lab.db" && -f "$PROD_HOME/cinevault-data/cinevault.db" ]]; then
  cp -f "$PROD_HOME/cinevault-data/cinevault.db" "$LAB/cinevault-data/cinemediavault-lab.db"
fi

cat > "$LAB/cinemediavault-lab.env" <<ENV
CINEVAULT_HOME="$LAB"
CINEVAULT_HOST=$HOST
CINEVAULT_PORT=$PORT
MOVIE_ROOT="/media/jnicolas/Expansion/Movies"
TV_ROOT="/media/jnicolas/Elements/TV Shows"
COMICS_ROOT="/media/jnicolas/Expansion/Comics"
COMIC_LIBRARY_DIR="/media/jnicolas/Expansion/Comics/comic-library"
MOVIE_APP_DIR="$LAB/media-download-library"
TV_APP_DIR="$LAB/tv-download-library"
MEDIA_LIBRARY_ASSET_DIR="$LAB/media-library-assets"
MEDIA_LIBRARY_LOG_DIR="$LAB/media-library-logs"
MEDIA_REFRESH_LOG_DIR="$LAB/media-library-refresh-logs"
TV_EPISODE_THUMB_DIR="$LAB/tv-download-library/episode-thumbnails"
HLS_CACHE_DIR="/tmp/cinemediavault-lab-hls"
MOBILE_DOWNLOAD_CACHE_DIR="$LAB/mobile-download-cache"
MOBILE_DOWNLOAD_CACHE_MAX_AGE_HOURS="24"
MOBILE_DOWNLOAD_TARGET_SOURCE_RATIO="0.40"
MOBILE_DOWNLOAD_MAX_OUTPUT_RATIO="0.98"
MOBILE_DOWNLOAD_MIN_EPISODE_MB="8"
CINEVAULT_DB="$LAB/cinevault-data/cinemediavault-lab.db"
CINEVAULT_BACKUP_DIR="$LAB/cinevault-data/backups"
CINEVAULT_PLAYBACK_MODE_FILE="$LAB/cinevault-data/playback-mode.txt"
CINEVAULT_MODULE_CONFIG_FILE="$LAB/cinevault-data/modules.json"
CINEVAULT_MODULE_LOGO_DIR="$LAB/cinevault-data/module-logos"
CINEVAULT_TLS_CERT="$LAB/certs/cinemediavault-lab.crt"
CINEVAULT_TLS_KEY="$LAB/certs/cinemediavault-lab.key"
ENV

if [[ ! -f "$LAB/certs/cinemediavault-lab.crt" || ! -f "$LAB/certs/cinemediavault-lab.key" ]]; then
  openssl req -x509 -newkey rsa:2048 -nodes -days 825 \
    -keyout "$LAB/certs/cinemediavault-lab.key" \
    -out "$LAB/certs/cinemediavault-lab.crt" \
    -subj "/CN=192.168.1.20" \
    -addext "subjectAltName=IP:192.168.1.20,DNS:localhost,DNS:NUC7i7BNH" >/dev/null 2>&1
fi

python3 - <<PY
from pathlib import Path
p = Path('$LAB/cinemediavault-lab-5000.py')
s = p.read_text()
if 'import ssl' not in s:
    s = s.replace('import sqlite3\\n', 'import sqlite3\\nimport ssl\\n')
old = '    print(f"Serving combined media library on http://{args.host}:{args.port}/", flush=True)\\n    server = ThreadingHTTPServer((args.host, args.port), CombinedHandler)\\n'
new = '''    tls_cert = os.environ.get("CINEVAULT_TLS_CERT", "")\\n    tls_key = os.environ.get("CINEVAULT_TLS_KEY", "")\\n    scheme = "https" if tls_cert and tls_key else "http"\\n    print(f"Serving combined media library on {scheme}://{args.host}:{args.port}/", flush=True)\\n    server = ThreadingHTTPServer((args.host, args.port), CombinedHandler)\\n    if tls_cert and tls_key:\\n        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)\\n        context.load_cert_chain(tls_cert, tls_key)\\n        server.socket = context.wrap_socket(server.socket, server_side=True)\\n'''
if 'CINEVAULT_TLS_CERT' not in s:
    if old not in s:
        raise SystemExit('Could not patch TLS server block')
    s = s.replace(old, new)
p.write_text(s)
PY

cat > "$LAB/start_lab_5000.sh" <<'START'
#!/usr/bin/env bash
set -euo pipefail
LAB="/home/jnicolas/cinemediavault-lab"
set -a
source "$LAB/cinemediavault-lab.env"
set +a
cd "$LAB"
PID_FILE="$LAB/cinemediavault-lab-5000.pid"
LOG_FILE="$LAB/logs/cinemediavault-lab-5000.log"
ERR_FILE="$LAB/logs/cinemediavault-lab-5000.err"
HEALTH_URL="https://127.0.0.1:${CINEVAULT_PORT:-5000}/login"

healthy() {
  curl -k -fsS --connect-timeout 2 --max-time 5 "$HEALTH_URL" >/dev/null 2>&1
}

stop_stale() {
  if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    kill "$(cat "$PID_FILE")" 2>/dev/null || true
    sleep 2
    kill -0 "$(cat "$PID_FILE")" 2>/dev/null && kill -9 "$(cat "$PID_FILE")" 2>/dev/null || true
  fi
  pgrep -f "^python3 $LAB/cinemediavault-lab-5000.py" | xargs -r kill 2>/dev/null || true
  sleep 1
  pgrep -f "^python3 $LAB/cinemediavault-lab-5000.py" | xargs -r kill -9 2>/dev/null || true
  rm -f "$PID_FILE"
}

if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  if healthy; then
    echo "CineMediaVault LAB already running PID $(cat "$PID_FILE")"
    echo "URL: https://192.168.1.20:${CINEVAULT_PORT:-5000}/"
    exit 0
  fi
  echo "CineMediaVault LAB PID $(cat "$PID_FILE") is unresponsive; restarting."
  stop_stale
fi

nohup python3 "$LAB/cinemediavault-lab-5000.py" --host "$CINEVAULT_HOST" --port "$CINEVAULT_PORT" >"$LOG_FILE" 2>"$ERR_FILE" &
echo $! > "$PID_FILE"
sleep 2
if kill -0 "$(cat "$PID_FILE")" 2>/dev/null && healthy; then
  echo "Started CineMediaVault LAB PID $(cat "$PID_FILE") - https://192.168.1.20:${CINEVAULT_PORT:-5000}/"
else
  echo "CineMediaVault LAB failed health check. See $ERR_FILE" >&2
  exit 1
fi
START

cat > "$LAB/stop_lab_5000.sh" <<STOP
#!/usr/bin/env bash
set -euo pipefail
LAB="$LAB"
PID_FILE="\$LAB/cinemediavault-lab-5000.pid"
if [[ -f "\$PID_FILE" ]] && kill -0 "\$(cat "\$PID_FILE")" 2>/dev/null; then
  kill "\$(cat "\$PID_FILE")" || true
  rm -f "\$PID_FILE"
  echo "Stopped CineMediaVault LAB."
else
  echo "CineMediaVault LAB is not running."
fi
pgrep -af ffmpeg | awk 'index(\$0, "/tmp/cinemediavault-lab-hls") {print \$1}' | xargs -r kill 2>/dev/null || true
STOP
chmod +x "$LAB/start_lab_5000.sh" "$LAB/stop_lab_5000.sh"
echo "Lab installed/refreshed: $LAB"
echo "Start: $LAB/start_lab_5000.sh"
echo "Stop: $LAB/stop_lab_5000.sh"
echo "URL: https://192.168.1.20:$PORT/"
