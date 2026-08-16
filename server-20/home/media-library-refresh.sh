#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd -P)"
if [[ -f "$SCRIPT_DIR/cinevault_common.sh" ]]; then
  # shellcheck source=cinevault_common.sh
  source "$SCRIPT_DIR/cinevault_common.sh"
  load_cinevault_env
fi

# ===== Adjustable settings =====
COMBINED_APP_DIR="${COMBINED_APP_DIR:-${CINEVAULT_HOME:-$SCRIPT_DIR}}"
MOVIE_APP_DIR="${MOVIE_APP_DIR:-$COMBINED_APP_DIR/media-download-library}"
TV_APP_DIR="${TV_APP_DIR:-$COMBINED_APP_DIR/tv-download-library}"
MOVIE_ROOT="${MOVIE_ROOT:-$COMBINED_APP_DIR/Movies}"
TV_ROOT="${TV_ROOT:-$COMBINED_APP_DIR/TV Shows}"
MOVIE_PORT="${MOVIE_PORT:-${CINEVAULT_PORT:-8093}}"
TV_PORT="${TV_PORT:-8095}"
COMBINED_PORT="${COMBINED_PORT:-${CINEVAULT_PORT:-8093}}"
RUN_COMBINED_SERVER="${RUN_COMBINED_SERVER:-1}"
POSTER_FETCH_LIMIT="${POSTER_FETCH_LIMIT:-0}"       # 0 = no limit; poster scripts skip existing mappings.
METADATA_FETCH_LIMIT="${METADATA_FETCH_LIMIT:-0}"   # 0 = no limit; metadata script skips existing mappings.
TMDB_SLEEP_SECONDS="${TMDB_SLEEP_SECONDS:-0.25}"
RUN_TV_POSTERS="${RUN_TV_POSTERS:-1}"
RUN_TV_METADATA="${RUN_TV_METADATA:-1}"
RUN_MOVIE_POSTERS="${RUN_MOVIE_POSTERS:-1}"
RUN_MOVIE_METADATA="${RUN_MOVIE_METADATA:-1}"
RUN_TV_THUMBNAILS="${RUN_TV_THUMBNAILS:-1}"
TV_THUMBNAIL_LIMIT="${TV_THUMBNAIL_LIMIT:-300}"
TV_EPISODE_THUMB_DIR="${TV_EPISODE_THUMB_DIR:-$TV_APP_DIR/episode-thumbnails}"
RESTART_SERVICES="${RESTART_SERVICES:-1}"
LOG_DIR="${LOG_DIR:-${MEDIA_REFRESH_LOG_DIR:-$COMBINED_APP_DIR/media-library-refresh-logs}}"
LOG_ROTATE_MAX_BYTES="${LOG_ROTATE_MAX_BYTES:-104857600}"
SCAN_PROGRESS_FILE="${SCAN_PROGRESS_FILE:-}"
# ===============================

mkdir -p "$LOG_DIR"
RUN_ID="$(date +%Y%m%d-%H%M%S)-$$"
LOG_FILE="$LOG_DIR/refresh-$RUN_ID.log"

log() {
  printf '%s %s\n' "$(date --iso-8601=seconds)" "$*" | tee -a "$LOG_FILE"
}

progress() {
  local percent="$1" phase="$2" message="$3"
  [[ -n "$SCAN_PROGRESS_FILE" ]] || return 0
  python3 - "$SCAN_PROGRESS_FILE" "$percent" "$phase" "$message" <<'PY'
import json, os, sys, time
path, percent, phase, message = sys.argv[1:]
data = {"running": True, "percent": int(percent), "phase": phase,
        "message": message, "updated_at": time.time()}
tmp = path + ".tmp"
os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
with open(tmp, "w", encoding="utf-8") as handle:
    json.dump(data, handle)
os.replace(tmp, path)
PY
}

rotate_logs() {
  find "$LOG_DIR" -type f ! -name '*.gz' -size +"${LOG_ROTATE_MAX_BYTES}"c -print0 |
    while IFS= read -r -d '' file; do
      gzip -f "$file"
    done
}

ensure_ffmpeg() {
  if command -v ffmpeg >/dev/null 2>&1; then
    log "ffmpeg found: $(command -v ffmpeg)"
    return 0
  fi
  log "ffmpeg missing; attempting package install"
  if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update | tee -a "$LOG_FILE"
    sudo apt-get install -y ffmpeg | tee -a "$LOG_FILE"
    return 0
  fi
  log "ffmpeg install skipped: unsupported package manager"
  return 1
}

snapshot_cache() {
  local cache="$1"
  local output="$2"
  if [[ -f "$cache" ]]; then
    cp -f "$cache" "$output"
  else
    printf '{}\n' > "$output"
  fi
}

summarize_movie_changes() {
  local before="$1"
  local after="$2"
  local report="$3"
  python3 - "$before" "$after" "$report" <<'PY'
import csv, json, sys
before, after, report = sys.argv[1:]
def movie_set(path):
    try:
        data = json.load(open(path, encoding="utf-8"))
    except Exception:
        return {}
    return {row["rel_path"]: row for row in data.get("movies", [])}
old = movie_set(before)
new = movie_set(after)
rows = []
for key in sorted(set(new) - set(old)):
    rows.append(["added", key, new[key].get("title",""), new[key].get("size","")])
for key in sorted(set(old) - set(new)):
    rows.append(["removed", key, old[key].get("title",""), old[key].get("size","")])
for key in sorted(set(old) & set(new)):
    if old[key].get("size") != new[key].get("size") or old[key].get("modified") != new[key].get("modified"):
        rows.append(["changed", key, new[key].get("title",""), new[key].get("size","")])
with open(report, "w", newline="", encoding="utf-8") as handle:
    writer = csv.writer(handle)
    writer.writerow(["status", "rel_path", "title", "size"])
    writer.writerows(rows)
print(len(rows))
PY
}

summarize_tv_changes() {
  local before="$1"
  local after="$2"
  local report="$3"
  python3 - "$before" "$after" "$report" <<'PY'
import csv, json, sys
before, after, report = sys.argv[1:]
def episode_set(path):
    try:
        data = json.load(open(path, encoding="utf-8"))
    except Exception:
        return {}
    return {row["rel_path"]: row for row in data.get("episodes", [])}
old = episode_set(before)
new = episode_set(after)
rows = []
for key in sorted(set(new) - set(old)):
    rows.append(["added", key, new[key].get("show",""), new[key].get("season",""), new[key].get("size","")])
for key in sorted(set(old) - set(new)):
    rows.append(["removed", key, old[key].get("show",""), old[key].get("season",""), old[key].get("size","")])
for key in sorted(set(old) & set(new)):
    if old[key].get("size") != new[key].get("size") or old[key].get("modified") != new[key].get("modified"):
        rows.append(["changed", key, new[key].get("show",""), new[key].get("season",""), new[key].get("size","")])
with open(report, "w", newline="", encoding="utf-8") as handle:
    writer = csv.writer(handle)
    writer.writerow(["status", "rel_path", "show", "season", "size"])
    writer.writerows(rows)
print(len(rows))
PY
}

log "Starting media library refresh"
progress 2 "Preparing" "Preparing the media scan"
log "Movie root: $MOVIE_ROOT"
log "TV root: $TV_ROOT"
rotate_logs
ensure_ffmpeg || true

MOVIE_BEFORE="$LOG_DIR/movie-before-$RUN_ID.json"
MOVIE_AFTER="$LOG_DIR/movie-after-$RUN_ID.json"
TV_BEFORE="$LOG_DIR/tv-before-$RUN_ID.json"
TV_AFTER="$LOG_DIR/tv-after-$RUN_ID.json"
MOVIE_CHANGE_REPORT="$LOG_DIR/movie-changes-$RUN_ID.csv"
TV_CHANGE_REPORT="$LOG_DIR/tv-changes-$RUN_ID.csv"

snapshot_cache "$MOVIE_APP_DIR/movie-live-index.json" "$MOVIE_BEFORE"
snapshot_cache "$TV_APP_DIR/tv-live-index.json" "$TV_BEFORE"

log "Refreshing movie index"
progress 8 "Movies" "Scanning movie files"
(
  cd "$MOVIE_APP_DIR"
  MOVIE_ROOT="$MOVIE_ROOT" \
  MOVIE_LIVE_CACHE="$MOVIE_APP_DIR/movie-live-index.json" \
  MOVIE_POSTER_MAP="$MOVIE_APP_DIR/poster-map.json" \
  MOVIE_POSTER_DIR="$MOVIE_APP_DIR/posters" \
  MOVIE_METADATA_MAP="$MOVIE_APP_DIR/movie-metadata-map.json" \
  python3 - <<'PY'
import importlib.util
spec = importlib.util.spec_from_file_location("m", "media_download_server.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
m.movie_index.refresh()
print(len(m.movie_index.items))
PY
) | tee -a "$LOG_FILE"

log "Refreshing TV index"
progress 20 "TV Shows" "Scanning shows, seasons, and episodes"
(
  cd "$TV_APP_DIR"
  TV_ROOT="$TV_ROOT" \
  TV_LIVE_CACHE="$TV_APP_DIR/tv-live-index.json" \
  TV_POSTER_MAP="$TV_APP_DIR/tv-poster-map.json" \
  TV_POSTER_DIR="$TV_APP_DIR/posters" \
  TV_METADATA_MAP="$TV_APP_DIR/tv-metadata-map.json" \
  python3 - <<'PY'
import importlib.util
spec = importlib.util.spec_from_file_location("t", "tv_download_server.py")
t = importlib.util.module_from_spec(spec)
spec.loader.exec_module(t)
t.tv_index.refresh()
print(len(t.tv_index.shows), len(t.tv_index.episode_by_id))
PY
) | tee -a "$LOG_FILE"

snapshot_cache "$MOVIE_APP_DIR/movie-live-index.json" "$MOVIE_AFTER"
snapshot_cache "$TV_APP_DIR/tv-live-index.json" "$TV_AFTER"

MOVIE_CHANGES="$(summarize_movie_changes "$MOVIE_BEFORE" "$MOVIE_AFTER" "$MOVIE_CHANGE_REPORT")"
TV_CHANGES="$(summarize_tv_changes "$TV_BEFORE" "$TV_AFTER" "$TV_CHANGE_REPORT")"
log "Movie changes: $MOVIE_CHANGES ($MOVIE_CHANGE_REPORT)"
log "TV changes: $TV_CHANGES ($TV_CHANGE_REPORT)"

if [[ "$RUN_MOVIE_POSTERS" == "1" ]]; then
  progress 34 "Movie artwork" "Finding missing movie posters"
  log "Fetching missing movie posters"
  (cd "$MOVIE_APP_DIR" && python3 fetch_tmdb_posters.py --limit "$POSTER_FETCH_LIMIT" --sleep "$TMDB_SLEEP_SECONDS") | tee -a "$LOG_FILE"
fi

if [[ "$RUN_MOVIE_METADATA" == "1" ]]; then
  progress 47 "Movie details" "Updating movie titles, cast, ratings, and genres"
  log "Fetching missing movie metadata"
  (cd "$MOVIE_APP_DIR" && python3 fetch_tmdb_movie_metadata.py --limit "$METADATA_FETCH_LIMIT" --sleep "$TMDB_SLEEP_SECONDS") | tee -a "$LOG_FILE"
fi

if [[ "$RUN_TV_POSTERS" == "1" ]]; then
  progress 60 "TV artwork" "Finding missing TV show posters"
  log "Fetching missing TV posters"
  (cd "$TV_APP_DIR" && python3 fetch_tmdb_tv_posters.py --limit "$POSTER_FETCH_LIMIT" --sleep "$TMDB_SLEEP_SECONDS") | tee -a "$LOG_FILE"
fi

if [[ "$RUN_TV_METADATA" == "1" ]]; then
  progress 72 "TV details" "Updating show and episode details"
  log "Fetching missing TV metadata"
  (cd "$TV_APP_DIR" && python3 fetch_tmdb_tv_metadata.py --limit "$METADATA_FETCH_LIMIT" --sleep "$TMDB_SLEEP_SECONDS") | tee -a "$LOG_FILE"
fi

if [[ "$RUN_TV_THUMBNAILS" == "1" ]]; then
  progress 86 "Episode artwork" "Generating missing episode thumbnails"
  log "Generating missing local TV episode thumbnails"
  (
    cd "$TV_APP_DIR"
    TV_APP_DIR="$TV_APP_DIR" \
    TV_EPISODE_THUMB_DIR="$TV_EPISODE_THUMB_DIR" \
    MEDIA_LIBRARY_LOG_DIR="$LOG_DIR" \
    python3 generate_tv_episode_thumbnails.py --limit "$TV_THUMBNAIL_LIMIT"
  ) | tee -a "$LOG_FILE"
fi

if [[ "$RESTART_SERVICES" == "1" ]]; then
  log "Restarting web apps"
  if [[ "$RUN_COMBINED_SERVER" == "1" && -x "$COMBINED_APP_DIR/start_media_library.sh" ]]; then
    (cd "$COMBINED_APP_DIR" && ./stop_media_library.sh >/dev/null 2>&1 || true && ./start_media_library.sh "$COMBINED_PORT") | tee -a "$LOG_FILE"
  elif [[ "$RUN_COMBINED_SERVER" == "1" && -x "$COMBINED_APP_DIR/start_lab_5000.sh" ]]; then
    (cd "$COMBINED_APP_DIR" && ./stop_lab_5000.sh >/dev/null 2>&1 || true && ./start_lab_5000.sh) | tee -a "$LOG_FILE"
  else
    (cd "$MOVIE_APP_DIR" && ./stop_movie_download_library.sh >/dev/null 2>&1 || true && ./start_movie_download_library.sh "$MOVIE_PORT") | tee -a "$LOG_FILE"
    (cd "$TV_APP_DIR" && ./stop_tv_download_library.sh >/dev/null 2>&1 || true && ./start_tv_download_library.sh "$TV_PORT") | tee -a "$LOG_FILE"
  fi
fi

progress 96 "Finalizing" "Saving indexes and reloading the library"
rotate_logs
log "Finished media library refresh"
