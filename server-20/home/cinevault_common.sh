#!/usr/bin/env bash
set -euo pipefail

script_dir() {
  cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1
  pwd -P
}

load_cinevault_env() {
  local base="${CINEVAULT_HOME:-$(script_dir)}"
  local env_file="${CINEVAULT_CONFIG:-$base/cinevault.env}"
  if [[ -f "$env_file" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$env_file"
    set +a
  fi

  export CINEVAULT_HOME="${CINEVAULT_HOME:-$base}"
  export CINEVAULT_HOST="${CINEVAULT_HOST:-0.0.0.0}"
  export CINEVAULT_PORT="${CINEVAULT_PORT:-8093}"
  export CINEVAULT_HLS_PORT="${CINEVAULT_HLS_PORT:-8096}"

  export MOVIE_ROOT="${MOVIE_ROOT:-$CINEVAULT_HOME/Movies}"
  export TV_ROOT="${TV_ROOT:-$CINEVAULT_HOME/TV Shows}"
  export COMICS_ROOT="${COMICS_ROOT:-$CINEVAULT_HOME/Comics}"
  export COMIC_LIBRARY_DIR="${COMIC_LIBRARY_DIR:-$COMICS_ROOT/comic-library}"

  export MOVIE_APP_DIR="${MOVIE_APP_DIR:-$CINEVAULT_HOME/media-download-library}"
  export TV_APP_DIR="${TV_APP_DIR:-$CINEVAULT_HOME/tv-download-library}"
  export MOVIE_HLS_APP_DIR="${MOVIE_HLS_APP_DIR:-$CINEVAULT_HOME/media-download-library-hls}"
  export TV_HLS_APP_DIR="${TV_HLS_APP_DIR:-$CINEVAULT_HOME/tv-download-library-hls}"
  export MEDIA_LIBRARY_ASSET_DIR="${MEDIA_LIBRARY_ASSET_DIR:-$CINEVAULT_HOME/media-library-assets}"

  export MEDIA_LIBRARY_LOG_DIR="${MEDIA_LIBRARY_LOG_DIR:-$CINEVAULT_HOME/media-library-logs}"
  export MEDIA_LIBRARY_HLS_LOG_DIR="${MEDIA_LIBRARY_HLS_LOG_DIR:-$CINEVAULT_HOME/media-library-hls-logs}"
  export MEDIA_REFRESH_LOG_DIR="${MEDIA_REFRESH_LOG_DIR:-$CINEVAULT_HOME/media-library-refresh-logs}"
  export TV_EPISODE_THUMB_DIR="${TV_EPISODE_THUMB_DIR:-$TV_APP_DIR/episode-thumbnails}"
  export HLS_CACHE_DIR="${HLS_CACHE_DIR:-/tmp/cinevault-hls-lab}"
}

ensure_dir() {
  mkdir -p "$1"
}

ensure_command() {
  local name="$1"
  if command -v "$name" >/dev/null 2>&1; then
    return 0
  fi
  if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update
    sudo apt-get install -y "$name"
    return 0
  fi
  printf 'Missing required command: %s\n' "$name" >&2
  return 1
}
