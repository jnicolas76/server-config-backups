#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_DIR="/etc/cinevault"
CONFIG_FILE="${CONFIG_DIR}/cinevault.conf"
INPUT_CONFIG_FILE=""
DEFAULT_HOME="/opt/cinevault"
DEFAULT_MOVIE_ROOT="/mnt/nfs-share-movies/Movies"
DEFAULT_TV_ROOT="/mnt/nfs-share-tvshows/TV Shows"
DEFAULT_COMICS_ROOT="/mnt/nfs-share-movies/Comics"
DEFAULT_COMIC_LIBRARY_ROOT="/mnt/nfs-share-movies/comic-library"

MODULES=()
NON_INTERACTIVE=0
SKIP_PACKAGES=0
ENABLE_SERVICES=1

usage() {
  cat <<'EOF'
Usage:
  sudo ./install.sh [options]

Options:
  --all                 Install all modules that have configured paths.
  --movies              Install/enable CineVault movie support.
  --tv                  Install/enable CineVault TV support.
  --comics              Install/build/serve comic library.
  --nes                 Install/build/serve NES library.
  --sega                Install/build/serve SEGA library.
  --dos                 Install/build/serve DOS library.
  --mame                Install/build/serve MAME library.
  --config PATH         Use an existing config file.
  --non-interactive     Do not prompt. Requires configured paths.
  --skip-packages       Do not install OS packages.
  --no-services         Copy/build only; do not enable systemd services.
  -h, --help            Show this help.

Examples:
  sudo ./install.sh
  sudo ./install.sh --movies --tv
  sudo ./install.sh --comics
  sudo ./install.sh --nes --non-interactive --config ./my.conf
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --all) MODULES=(movies tv comics nes sega dos mame);;
    --movies) MODULES+=("movies");;
    --tv) MODULES+=("tv");;
    --comics) MODULES+=("comics");;
    --nes) MODULES+=("nes");;
    --sega) MODULES+=("sega");;
    --dos) MODULES+=("dos");;
    --mame) MODULES+=("mame");;
    --config) INPUT_CONFIG_FILE="$2"; shift;;
    --non-interactive) NON_INTERACTIVE=1;;
    --skip-packages) SKIP_PACKAGES=1;;
    --no-services) ENABLE_SERVICES=0;;
    -h|--help) usage; exit 0;;
    *) echo "Unknown option: $1" >&2; usage; exit 2;;
  esac
  shift
done

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root: sudo $0" >&2
  exit 1
fi

ask() {
  local var="$1"
  local prompt="$2"
  local default="${3:-}"
  local current="${!var:-}"
  if [[ -n "$current" ]]; then
    default="$current"
  fi
  if [[ "$NON_INTERACTIVE" -eq 1 ]]; then
    printf -v "$var" '%s' "$default"
    return
  fi
  local answer
  read -r -p "${prompt} [${default}]: " answer
  printf -v "$var" '%s' "${answer:-$default}"
}

truthy() {
  [[ "${1:-}" == "1" || "${1:-}" == "true" || "${1:-}" == "yes" || "${1:-}" == "on" ]]
}

module_selected() {
  local wanted="$1"
  local item
  for item in "${MODULES[@]:-}"; do
    [[ "$item" == "$wanted" ]] && return 0
  done
  return 1
}

load_or_create_config() {
  mkdir -p "$CONFIG_DIR"
  local source_config="$CONFIG_FILE"
  if [[ -n "$INPUT_CONFIG_FILE" ]]; then
    source_config="$INPUT_CONFIG_FILE"
  fi
  if [[ -f "$source_config" ]]; then
    # shellcheck disable=SC1090
    source "$source_config"
  fi

  CINEVAULT_HOME="${CINEVAULT_HOME:-$DEFAULT_HOME}"
  CINEVAULT_USER="${CINEVAULT_USER:-cinevault}"
  CINEVAULT_HOST="${CINEVAULT_HOST:-0.0.0.0}"
  CINEVAULT_PORT="${CINEVAULT_PORT:-8093}"
  CINEVAULT_LAB_PORT="${CINEVAULT_LAB_PORT:-5000}"
  CINEVAULT_NAME="${CINEVAULT_NAME:-CineMediaVault}"
  CINEVAULT_BACKUP_KEEP="${CINEVAULT_BACKUP_KEEP:-10}"
  COMICS_PORT="${COMICS_PORT:-8110}"
  NES_PORT="${NES_PORT:-8092}"
  SEGA_PORT="${SEGA_PORT:-8094}"
  DOS_PORT="${DOS_PORT:-8091}"
  MAME_PORT="${MAME_PORT:-8101}"

  if [[ "$NON_INTERACTIVE" -eq 0 ]]; then
    ask CINEVAULT_HOME "Install directory" "$CINEVAULT_HOME"
    ask CINEVAULT_USER "Service user" "$CINEVAULT_USER"
    ask CINEVAULT_PORT "CineVault port" "$CINEVAULT_PORT"
    ask CINEVAULT_LAB_PORT "CineVault lab port" "$CINEVAULT_LAB_PORT"
    ask MOVIE_ROOT "Movies path, blank to skip" "${MOVIE_ROOT:-$DEFAULT_MOVIE_ROOT}"
    ask TV_ROOT "TV Shows path, blank to skip" "${TV_ROOT:-$DEFAULT_TV_ROOT}"
    ask COMICS_ROOT "Comics source path, blank to skip" "${COMICS_ROOT:-$DEFAULT_COMICS_ROOT}"
    ask COMIC_LIBRARY_ROOT "Built comic-library output path" "${COMIC_LIBRARY_ROOT:-$DEFAULT_COMIC_LIBRARY_ROOT}"
    ask NES_ROOT "NES ROM/source path, blank to skip" "${NES_ROOT:-}"
    ask SEGA_ROOT "SEGA ROM/source path, blank to skip" "${SEGA_ROOT:-}"
    ask DOS_ROOT "DOS archive/source path, blank to skip" "${DOS_ROOT:-}"
    ask MAME_ROOT "MAME ROM/source path, blank to skip" "${MAME_ROOT:-}"
  fi

  INSTALL_MOVIES="${INSTALL_MOVIES:-0}"
  INSTALL_TV="${INSTALL_TV:-0}"
  INSTALL_COMICS="${INSTALL_COMICS:-0}"
  INSTALL_NES="${INSTALL_NES:-0}"
  INSTALL_SEGA="${INSTALL_SEGA:-0}"
  INSTALL_DOS="${INSTALL_DOS:-0}"
  INSTALL_MAME="${INSTALL_MAME:-0}"

  if [[ "${#MODULES[@]}" -eq 0 ]]; then
    [[ -n "${MOVIE_ROOT:-}" ]] && INSTALL_MOVIES=1
    [[ -n "${TV_ROOT:-}" ]] && INSTALL_TV=1
    [[ -n "${COMICS_ROOT:-}" || -n "${COMIC_LIBRARY_ROOT:-}" ]] && INSTALL_COMICS=1
    [[ -n "${NES_ROOT:-}" ]] && INSTALL_NES=1
    [[ -n "${SEGA_ROOT:-}" ]] && INSTALL_SEGA=1
    [[ -n "${DOS_ROOT:-}" ]] && INSTALL_DOS=1
    [[ -n "${MAME_ROOT:-}" ]] && INSTALL_MAME=1
  else
    INSTALL_MOVIES=0; INSTALL_TV=0; INSTALL_COMICS=0; INSTALL_NES=0; INSTALL_SEGA=0; INSTALL_DOS=0; INSTALL_MAME=0
    module_selected movies && INSTALL_MOVIES=1
    module_selected tv && INSTALL_TV=1
    module_selected comics && INSTALL_COMICS=1
    module_selected nes && INSTALL_NES=1
    module_selected sega && INSTALL_SEGA=1
    module_selected dos && INSTALL_DOS=1
    module_selected mame && INSTALL_MAME=1
  fi

  cat > "$CONFIG_FILE" <<EOF
CINEVAULT_HOME="${CINEVAULT_HOME}"
CINEVAULT_USER="${CINEVAULT_USER}"
CINEVAULT_HOST="${CINEVAULT_HOST}"
CINEVAULT_PORT="${CINEVAULT_PORT}"
CINEVAULT_LAB_PORT="${CINEVAULT_LAB_PORT}"
CINEVAULT_NAME="${CINEVAULT_NAME}"
CINEVAULT_BACKUP_KEEP="${CINEVAULT_BACKUP_KEEP}"
MOVIE_ROOT="${MOVIE_ROOT:-}"
TV_ROOT="${TV_ROOT:-}"
COMICS_ROOT="${COMICS_ROOT:-}"
COMIC_LIBRARY_ROOT="${COMIC_LIBRARY_ROOT:-}"
NES_ROOT="${NES_ROOT:-}"
SEGA_ROOT="${SEGA_ROOT:-}"
DOS_ROOT="${DOS_ROOT:-}"
MAME_ROOT="${MAME_ROOT:-}"
COMICS_PORT="${COMICS_PORT}"
NES_PORT="${NES_PORT}"
SEGA_PORT="${SEGA_PORT}"
DOS_PORT="${DOS_PORT}"
MAME_PORT="${MAME_PORT}"
INSTALL_MOVIES="${INSTALL_MOVIES}"
INSTALL_TV="${INSTALL_TV}"
INSTALL_COMICS="${INSTALL_COMICS}"
INSTALL_NES="${INSTALL_NES}"
INSTALL_SEGA="${INSTALL_SEGA}"
INSTALL_DOS="${INSTALL_DOS}"
INSTALL_MAME="${INSTALL_MAME}"
EOF
}

install_packages() {
  [[ "$SKIP_PACKAGES" -eq 1 ]] && return
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-venv python3-pip unzip zip p7zip-full poppler-utils rsync
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y python3 python3-pip unzip zip p7zip p7zip-plugins poppler-utils rsync
  elif command -v yum >/dev/null 2>&1; then
    yum install -y python3 python3-pip unzip zip p7zip p7zip-plugins poppler-utils rsync
  else
    echo "No supported package manager found. Install python3, unzip, zip, p7zip, poppler-utils, rsync manually." >&2
  fi
}

install_user() {
  if ! id "$CINEVAULT_USER" >/dev/null 2>&1; then
    useradd --system --create-home --shell /usr/sbin/nologin "$CINEVAULT_USER"
  fi
}

copy_payload() {
  mkdir -p "$CINEVAULT_HOME"
  rsync -a --delete "$SCRIPT_DIR/app/" "$CINEVAULT_HOME/"
  mkdir -p "$CINEVAULT_HOME/media-download-library" "$CINEVAULT_HOME/tv-download-library" "$CINEVAULT_HOME/bin" "$CINEVAULT_HOME/state" "$CINEVAULT_HOME/backups" "$CINEVAULT_HOME/hls-cache"
  cp "$CINEVAULT_HOME/cinevault/media_download_server.py" "$CINEVAULT_HOME/media-download-library/media_download_server.py"
  cp "$CINEVAULT_HOME/cinevault/tv_download_server.py" "$CINEVAULT_HOME/tv-download-library/tv_download_server.py"
  cp "$CINEVAULT_HOME/cinevault/media-library-server.py" "$CINEVAULT_HOME/media-library-server.py"
  mkdir -p "$CINEVAULT_HOME/media-download-library/posters" "$CINEVAULT_HOME/tv-download-library/posters"
  cp "$CINEVAULT_HOME/cinevault/"*.jpg "$CINEVAULT_HOME/tv-download-library/posters/" 2>/dev/null || true
  cp "$CINEVAULT_HOME/cinevault/custom-art-map.json" "$CINEVAULT_HOME/tv-download-library/custom-art-map.json" 2>/dev/null || true
}

write_runtime_scripts() {
  cat > "$CINEVAULT_HOME/bin/start-cinevault" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
source /etc/cinevault/cinevault.conf
export MOVIE_APP_DIR="${CINEVAULT_HOME}/media-download-library"
export TV_APP_DIR="${CINEVAULT_HOME}/tv-download-library"
export MOVIE_ROOT
export TV_ROOT
export CINEVAULT_SERVER_NAME="${CINEVAULT_NAME}"
export MOVIE_LIVE_CACHE="${CINEVAULT_HOME}/state/movie-live-index.json"
export TV_LIVE_CACHE="${CINEVAULT_HOME}/state/tv-live-index.json"
export MOVIE_POSTER_MAP="${CINEVAULT_HOME}/media-download-library/poster-map.json"
export TV_POSTER_MAP="${CINEVAULT_HOME}/tv-download-library/tv-poster-map.json"
export MOVIE_POSTER_DIR="${CINEVAULT_HOME}/media-download-library/posters"
export TV_POSTER_DIR="${CINEVAULT_HOME}/tv-download-library/posters"
export MOVIE_METADATA_MAP="${CINEVAULT_HOME}/media-download-library/movie-metadata-map.json"
export TV_METADATA_MAP="${CINEVAULT_HOME}/tv-download-library/tv-metadata-map.json"
export CINEVAULT_DB="${CINEVAULT_HOME}/state/cinevault.db"
export CINEVAULT_BACKUP_DIR="${CINEVAULT_HOME}/backups"
export CINEVAULT_BACKUP_KEEP="${CINEVAULT_BACKUP_KEEP:-10}"
export HLS_CACHE_DIR="${CINEVAULT_HOME}/hls-cache"
exec python3 "${CINEVAULT_HOME}/media-library-server.py" --host "${CINEVAULT_HOST}" --port "${CINEVAULT_PORT}"
EOF

  cat > "$CINEVAULT_HOME/bin/start-cinevault-lab" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
source /etc/cinevault/cinevault.conf
export MOVIE_APP_DIR="${CINEVAULT_HOME}/media-download-library"
export TV_APP_DIR="${CINEVAULT_HOME}/tv-download-library"
export MOVIE_ROOT
export TV_ROOT
export CINEVAULT_SERVER_NAME="${CINEVAULT_NAME} Lab"
export MOVIE_LIVE_CACHE="${CINEVAULT_HOME}/state/lab-movie-live-index.json"
export TV_LIVE_CACHE="${CINEVAULT_HOME}/state/lab-tv-live-index.json"
export MOVIE_POSTER_MAP="${CINEVAULT_HOME}/media-download-library/poster-map.json"
export TV_POSTER_MAP="${CINEVAULT_HOME}/tv-download-library/tv-poster-map.json"
export MOVIE_POSTER_DIR="${CINEVAULT_HOME}/media-download-library/posters"
export TV_POSTER_DIR="${CINEVAULT_HOME}/tv-download-library/posters"
export MOVIE_METADATA_MAP="${CINEVAULT_HOME}/media-download-library/movie-metadata-map.json"
export TV_METADATA_MAP="${CINEVAULT_HOME}/tv-download-library/tv-metadata-map.json"
export CINEVAULT_DB="${CINEVAULT_HOME}/state/cinevault-lab.db"
export CINEVAULT_BACKUP_DIR="${CINEVAULT_HOME}/backups/lab"
export CINEVAULT_BACKUP_KEEP="${CINEVAULT_BACKUP_KEEP:-10}"
export HLS_CACHE_DIR="${CINEVAULT_HOME}/hls-cache-lab"
export MOBILE_DOWNLOAD_CACHE_DIR="${CINEVAULT_HOME}/mobile-download-cache-lab"
export MOBILE_DOWNLOAD_TARGET_SOURCE_RATIO="${MOBILE_DOWNLOAD_TARGET_SOURCE_RATIO:-0.40}"
export MOBILE_DOWNLOAD_ABSOLUTE_MAX_OUTPUT_RATIO="${MOBILE_DOWNLOAD_ABSOLUTE_MAX_OUTPUT_RATIO:-1.00}"
export MOBILE_DOWNLOAD_TARGET_TOLERANCE="${MOBILE_DOWNLOAD_TARGET_TOLERANCE:-1.08}"
exec python3 "${CINEVAULT_HOME}/cinevault/cinemediavault-lab-5000.py" --host "${CINEVAULT_HOST}" --port "${CINEVAULT_LAB_PORT:-5000}"
EOF

  cat > "$CINEVAULT_HOME/bin/stop-cinevault-lab" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
source /etc/cinevault/cinevault.conf
HLS_CACHE_DIR="${HLS_CACHE_DIR:-${CINEVAULT_HOME}/hls-cache-lab}"
pattern="^python3 ${CINEVAULT_HOME}/cinevault/cinemediavault-lab-5000.py"
pids="$(pgrep -f "$pattern" 2>/dev/null || true)"
if [[ -n "$pids" ]]; then
  kill $pids 2>/dev/null || true
  sleep 2
  pids="$(pgrep -f "$pattern" 2>/dev/null || true)"
  [[ -z "$pids" ]] || kill -9 $pids 2>/dev/null || true
fi
if [[ -n "${HLS_CACHE_DIR:-}" ]]; then
  pgrep -af ffmpeg | awk -v cache="${HLS_CACHE_DIR}" 'index($0, cache) {print $1}' | xargs -r kill 2>/dev/null || true
fi
EOF

  cat > "$CINEVAULT_HOME/bin/backup-cinevault-db" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
source /etc/cinevault/cinevault.conf
export CINEVAULT_DB="${CINEVAULT_HOME}/state/cinevault.db"
export CINEVAULT_BACKUP_DIR="${CINEVAULT_HOME}/backups"
export CINEVAULT_BACKUP_KEEP="${CINEVAULT_BACKUP_KEEP:-10}"
exec python3 "${CINEVAULT_HOME}/cinevault/cinevault_db_backup.py"
EOF

  cat > "$CINEVAULT_HOME/bin/start-static-module" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
source /etc/cinevault/cinevault.conf
module="${1:?module required}"
case "$module" in
  comics) root="${COMIC_LIBRARY_ROOT}"; port="${COMICS_PORT}" ;;
  nes) root="${CINEVAULT_HOME}/games/nes"; port="${NES_PORT}" ;;
  sega) root="${CINEVAULT_HOME}/games/sega"; port="${SEGA_PORT}" ;;
  dos) root="${CINEVAULT_HOME}/games/dos"; port="${DOS_PORT}" ;;
  mame) root="${CINEVAULT_HOME}/games/mame"; port="${MAME_PORT}" ;;
  *) echo "Unknown module: $module" >&2; exit 2 ;;
esac
exec python3 "${CINEVAULT_HOME}/static_file_server.py" --host "${CINEVAULT_HOST}" --port "$port" --root "$root"
EOF

  cat > "$CINEVAULT_HOME/bin/rebuild-module" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
source /etc/cinevault/cinevault.conf
module="${1:?module required}"
case "$module" in
  comics)
    : "${COMICS_ROOT:?COMICS_ROOT is required}"
    : "${COMIC_LIBRARY_ROOT:?COMIC_LIBRARY_ROOT is required}"
    COMICS_ROOT="$COMICS_ROOT" COMIC_LIBRARY_ROOT="$COMIC_LIBRARY_ROOT" python3 "${CINEVAULT_HOME}/scripts/comics/build_new_comic_collections.py"
    ;;
  nes|sega|dos|mame)
    var="$(echo "$module" | tr '[:lower:]' '[:upper:]')_ROOT"
    source_root="${!var:-}"
    : "${source_root:?${var} is required}"
    target="${CINEVAULT_HOME}/games/${module}"
    mkdir -p "$target/source" "$target/roms"
    rsync -a --delete "$source_root"/ "$target/source"/
    cp -a "${CINEVAULT_HOME}/scripts/games/${module}/." "$target/"
    python3 "$target/build_library.py" --source "$target/source" --rom-dir "$target/roms" --catalog "$target/catalog.json"
    ;;
  *) echo "Unknown module: $module" >&2; exit 2 ;;
esac
EOF
  chmod +x "$CINEVAULT_HOME/bin/start-cinevault" "$CINEVAULT_HOME/bin/start-cinevault-lab" "$CINEVAULT_HOME/bin/stop-cinevault-lab" "$CINEVAULT_HOME/bin/backup-cinevault-db" "$CINEVAULT_HOME/bin/start-static-module" "$CINEVAULT_HOME/bin/rebuild-module"
}

patch_comic_scripts() {
  local script
  for script in "$CINEVAULT_HOME"/scripts/comics/*.py; do
    [[ -f "$script" ]] || continue
    sed -i 's|COMICS = Path("/home/jnicolas/Data9/Comics")|COMICS = Path(os.environ.get("COMICS_ROOT", "/home/jnicolas/Data9/Comics"))|g' "$script"
    sed -i 's|LIBRARY = Path("/home/jnicolas/Data9/comic-library")|LIBRARY = Path(os.environ.get("COMIC_LIBRARY_ROOT", "/home/jnicolas/Data9/comic-library"))|g' "$script"
    sed -i 's|path = Path("/home/jnicolas/build_new_comic_collections.py")|path = Path(os.environ.get("COMIC_BASE_BUILDER", str(Path(__file__).resolve().parent / "build_new_comic_collections.py")))|g' "$script"
  done
}

install_systemd() {
  [[ "$ENABLE_SERVICES" -eq 1 ]] || return
  local service_tmp static_tmp
  service_tmp="$(mktemp)"
  static_tmp="$(mktemp)"
  sed "s|__CINEVAULT_HOME__|${CINEVAULT_HOME}|g; s|__CINEVAULT_USER__|${CINEVAULT_USER}|g" "$SCRIPT_DIR/systemd/cinevault.service.template" > "$service_tmp"
  sed "s|__CINEVAULT_HOME__|${CINEVAULT_HOME}|g; s|__CINEVAULT_USER__|${CINEVAULT_USER}|g" "$SCRIPT_DIR/systemd/cinevault-static@.service.template" > "$static_tmp"
  install -m 0644 "$service_tmp" /etc/systemd/system/cinevault.service
  install -m 0644 "$static_tmp" /etc/systemd/system/cinevault-static@.service
  rm -f "$service_tmp" "$static_tmp"
  cat > /etc/systemd/system/cinevault-db-backup.service <<EOF
[Unit]
Description=CineVault SQLite database backup

[Service]
Type=oneshot
EnvironmentFile=${CONFIG_FILE}
ExecStart=${CINEVAULT_HOME}/bin/backup-cinevault-db
User=${CINEVAULT_USER}
Group=${CINEVAULT_USER}
EOF
  cat > /etc/systemd/system/cinevault-db-backup.timer <<'EOF'
[Unit]
Description=Daily CineVault SQLite database backup

[Timer]
OnCalendar=*-*-* 01:00:00
Persistent=true

[Install]
WantedBy=timers.target
EOF
  systemctl daemon-reload
  if truthy "$INSTALL_MOVIES" || truthy "$INSTALL_TV"; then
    systemctl enable --now cinevault.service
    systemctl enable --now cinevault-db-backup.timer
  fi
  truthy "$INSTALL_COMICS" && [[ -n "${COMIC_LIBRARY_ROOT:-}" ]] && systemctl enable --now cinevault-static@comics.service || true
  truthy "$INSTALL_NES" && systemctl enable --now cinevault-static@nes.service || true
  truthy "$INSTALL_SEGA" && systemctl enable --now cinevault-static@sega.service || true
  truthy "$INSTALL_DOS" && systemctl enable --now cinevault-static@dos.service || true
  truthy "$INSTALL_MAME" && systemctl enable --now cinevault-static@mame.service || true
}

build_requested_modules() {
  if truthy "$INSTALL_COMICS"; then "$CINEVAULT_HOME/bin/rebuild-module" comics; fi
  if truthy "$INSTALL_NES"; then "$CINEVAULT_HOME/bin/rebuild-module" nes; fi
  if truthy "$INSTALL_SEGA"; then "$CINEVAULT_HOME/bin/rebuild-module" sega; fi
  if truthy "$INSTALL_DOS"; then "$CINEVAULT_HOME/bin/rebuild-module" dos; fi
  if truthy "$INSTALL_MAME"; then "$CINEVAULT_HOME/bin/rebuild-module" mame; fi
}

load_or_create_config
install_packages
install_user
copy_payload
patch_comic_scripts
write_runtime_scripts
chown -R "$CINEVAULT_USER:$CINEVAULT_USER" "$CINEVAULT_HOME"
build_requested_modules
chown -R "$CINEVAULT_USER:$CINEVAULT_USER" "$CINEVAULT_HOME" "${COMIC_LIBRARY_ROOT:-$CINEVAULT_HOME}" 2>/dev/null || true
install_systemd

echo "Installed CineVault portable bundle."
echo "Config: $CONFIG_FILE"
echo "Home:   $CINEVAULT_HOME"
echo "Main:   http://$(hostname -I | awk '{print $1}'):${CINEVAULT_PORT}/"
echo "Lab:    http://$(hostname -I | awk '{print $1}'):${CINEVAULT_LAB_PORT}/"
