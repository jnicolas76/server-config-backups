#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd -P)"
if [[ ! -f "$SCRIPT_DIR/cinevault.env" ]]; then
  cp "$SCRIPT_DIR/cinevault.env.example" "$SCRIPT_DIR/cinevault.env"
  echo "Created $SCRIPT_DIR/cinevault.env from template. Edit it if your paths are different."
fi

# shellcheck source=cinevault_common.sh
source "$SCRIPT_DIR/cinevault_common.sh"
load_cinevault_env

INSTALL_PRIMARY="${INSTALL_PRIMARY:-1}"
INSTALL_HLS="${INSTALL_HLS:-1}"
INSTALL_REFRESH="${INSTALL_REFRESH:-1}"
INSTALL_STATS="${INSTALL_STATS:-1}"
INSTALL_ANDROID="${INSTALL_ANDROID:-0}"

ensure_command python3
ensure_command ffmpeg || true
ensure_command gzip || true

ensure_dir "$CINEVAULT_HOME"
ensure_dir "$MOVIE_APP_DIR"
ensure_dir "$TV_APP_DIR"
ensure_dir "$MOVIE_HLS_APP_DIR"
ensure_dir "$TV_HLS_APP_DIR"
ensure_dir "$MEDIA_LIBRARY_LOG_DIR"
ensure_dir "$MEDIA_LIBRARY_HLS_LOG_DIR"
ensure_dir "$MEDIA_REFRESH_LOG_DIR"
ensure_dir "$MEDIA_LIBRARY_ASSET_DIR"
ensure_dir "$TV_EPISODE_THUMB_DIR"
ensure_dir "$HLS_CACHE_DIR"

copy_if_exists() {
  local src="$1"
  local dst="$2"
  [[ -f "$src" ]] || return 0
  cp -f "$src" "$dst"
}

if [[ "$INSTALL_PRIMARY" == "1" ]]; then
  copy_if_exists "$SCRIPT_DIR/media-library-server.py" "$CINEVAULT_HOME/media-library-server.py"
  copy_if_exists "$SCRIPT_DIR/media_download_server.py" "$MOVIE_APP_DIR/media_download_server.py"
  copy_if_exists "$SCRIPT_DIR/tv_download_server.py" "$TV_APP_DIR/tv_download_server.py"
  copy_if_exists "$SCRIPT_DIR/fetch_tmdb_movie_metadata.py" "$MOVIE_APP_DIR/fetch_tmdb_movie_metadata.py"
  copy_if_exists "$SCRIPT_DIR/fetch_tmdb_posters.py" "$MOVIE_APP_DIR/fetch_tmdb_posters.py"
  copy_if_exists "$SCRIPT_DIR/fetch_tmdb_tv_metadata.py" "$TV_APP_DIR/fetch_tmdb_tv_metadata.py"
  copy_if_exists "$SCRIPT_DIR/fetch_tmdb_tv_posters.py" "$TV_APP_DIR/fetch_tmdb_tv_posters.py"
  copy_if_exists "$SCRIPT_DIR/generate_tv_episode_thumbnails.py" "$TV_APP_DIR/generate_tv_episode_thumbnails.py"
fi

if [[ "$INSTALL_HLS" == "1" ]]; then
  copy_if_exists "$SCRIPT_DIR/media-library-server-hls.py" "$CINEVAULT_HOME/media-library-server-hls.py"
  copy_if_exists "$SCRIPT_DIR/media_download_server.py" "$MOVIE_HLS_APP_DIR/media_download_server.py"
  copy_if_exists "$SCRIPT_DIR/tv_download_server.py" "$TV_HLS_APP_DIR/tv_download_server.py"
fi

copy_if_exists "$SCRIPT_DIR/cinevault_common.sh" "$CINEVAULT_HOME/cinevault_common.sh"
copy_if_exists "$SCRIPT_DIR/cinevault.env" "$CINEVAULT_HOME/cinevault.env"
copy_if_exists "$SCRIPT_DIR/start_media_library.sh" "$CINEVAULT_HOME/start_media_library.sh"
copy_if_exists "$SCRIPT_DIR/stop_media_library.sh" "$CINEVAULT_HOME/stop_media_library.sh"
copy_if_exists "$SCRIPT_DIR/start_media_library_hls.sh" "$CINEVAULT_HOME/start_media_library_hls.sh"
copy_if_exists "$SCRIPT_DIR/stop_media_library_hls.sh" "$CINEVAULT_HOME/stop_media_library_hls.sh"
copy_if_exists "$SCRIPT_DIR/media-library-refresh.sh" "$CINEVAULT_HOME/media-library-refresh.sh"
copy_if_exists "$SCRIPT_DIR/install_media_refresh_cron.sh" "$CINEVAULT_HOME/install_media_refresh_cron.sh"
copy_if_exists "$SCRIPT_DIR/media-library-refresh-README.md" "$CINEVAULT_HOME/media-library-refresh-README.md"
copy_if_exists "$SCRIPT_DIR/cinevault_stats.sh" "$CINEVAULT_HOME/cinevault_stats.sh"

chmod +x "$CINEVAULT_HOME"/{start_media_library.sh,stop_media_library.sh,start_media_library_hls.sh,stop_media_library_hls.sh,media-library-refresh.sh,install_media_refresh_cron.sh,cinevault_stats.sh,cinevault_common.sh} 2>/dev/null || true

if [[ "$INSTALL_REFRESH" == "1" ]]; then
  CINEVAULT_CONFIG="$CINEVAULT_HOME/cinevault.env" "$CINEVAULT_HOME/install_media_refresh_cron.sh"
fi

if [[ "$INSTALL_ANDROID" == "1" && -d "$SCRIPT_DIR/android/CineVaultCompanion" ]]; then
  ensure_dir "$CINEVAULT_HOME/android"
  cp -a "$SCRIPT_DIR/android/CineVaultCompanion" "$CINEVAULT_HOME/android/"
fi

cat <<EOF
CineVault install complete.

Config: $CINEVAULT_HOME/cinevault.env
Primary: $CINEVAULT_HOME/start_media_library.sh $CINEVAULT_PORT
HLS lab: $CINEVAULT_HOME/start_media_library_hls.sh $CINEVAULT_HLS_PORT
Refresh: $CINEVAULT_HOME/media-library-refresh.sh
Stats: $CINEVAULT_HOME/cinevault_stats.sh
EOF
