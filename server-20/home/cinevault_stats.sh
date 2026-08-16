#!/usr/bin/env bash
set -euo pipefail

# CineVault library and service statistics.
# Adjust these paths if the library moves to a different server.

MOVIE_ROOT="${MOVIE_ROOT:-/home/jnicolas/Data4/Movies}"
TV_ROOT="${TV_ROOT:-/home/jnicolas/Data2/TV Shows}"
MOVIE_APP_DIR="${MOVIE_APP_DIR:-/home/jnicolas/media-download-library}"
TV_APP_DIR="${TV_APP_DIR:-/home/jnicolas/tv-download-library}"
HLS_CACHE_DIR="${HLS_CACHE_DIR:-/tmp/cinevault-hls}"
HLS_LAB_CACHE_DIR="${HLS_LAB_CACHE_DIR:-/tmp/cinevault-hls-lab}"
LOG_DIR="${LOG_DIR:-/home/jnicolas/media-library-refresh-logs}"
COMICS_DIR="${COMICS_DIR:-/home/jnicolas/Data4/Comics}"
COMIC_LIBRARY_DIR="${COMIC_LIBRARY_DIR:-/home/jnicolas/Data4/Comics/comic-library}"

MOVIE_LIVE_CACHE="${MOVIE_LIVE_CACHE:-$MOVIE_APP_DIR/movie-live-index.json}"
TV_LIVE_CACHE="${TV_LIVE_CACHE:-$TV_APP_DIR/tv-live-index.json}"
MOVIE_POSTER_DIR="${MOVIE_POSTER_DIR:-$MOVIE_APP_DIR/posters}"
TV_POSTER_DIR="${TV_POSTER_DIR:-$TV_APP_DIR/posters}"
TV_EPISODE_THUMB_DIR="${TV_EPISODE_THUMB_DIR:-$TV_APP_DIR/episode-thumbnails}"
MOVIE_METADATA_MAP="${MOVIE_METADATA_MAP:-$MOVIE_APP_DIR/movie-metadata-map.json}"
TV_METADATA_MAP="${TV_METADATA_MAP:-$TV_APP_DIR/tv-metadata-map.json}"

VIDEO_EXT_REGEX='\.(mp4|mkv|m4v|avi|mov|mpeg|mpg|m2ts|ts|webm)$'

human_size() {
  local path="$1"
  if [[ -e "$path" ]]; then
    du -sh "$path" 2>/dev/null | awk '{print $1}'
  else
    printf 'missing'
  fi
}

bytes_to_human() {
  numfmt --to=iec --suffix=B "$1" 2>/dev/null || printf '%sB' "$1"
}

section() {
  printf '\n==== %s ====\n' "$1"
}

count_files() {
  local root="$1"
  local pattern="$2"
  if [[ ! -d "$root" ]]; then
    printf '0'
    return
  fi
  find "$root" -type f | grep -Eic "$pattern" || true
}

json_stat() {
  local script="$1"
  python3 - "$script" <<'PY'
import json, pathlib, sys
code = sys.argv[1]
ns = {}
exec(code, ns, ns)
PY
}

printf 'CineVault Statistics - %s\n' "$(date --iso-8601=seconds)"
printf 'Host: %s\n' "$(hostname)"

section "Configured Paths"
printf 'Movies root: %s (%s)\n' "$MOVIE_ROOT" "$(human_size "$MOVIE_ROOT")"
printf 'TV root: %s (%s)\n' "$TV_ROOT" "$(human_size "$TV_ROOT")"
printf 'Movie app: %s (%s)\n' "$MOVIE_APP_DIR" "$(human_size "$MOVIE_APP_DIR")"
printf 'TV app: %s (%s)\n' "$TV_APP_DIR" "$(human_size "$TV_APP_DIR")"
printf 'Logs: %s (%s)\n' "$LOG_DIR" "$(human_size "$LOG_DIR")"

section "Running CineVault Services"
pgrep -af 'media-library-server.*809[36]' || printf 'No CineVault 8093/8096 processes found.\n'
if command -v ss >/dev/null 2>&1; then
  printf '\nListening ports 8093/8096:\n'
  ss -ltnp 2>/dev/null | awk '$4 ~ /:8093$|:8096$/ {print}'
fi

section "Cron"
crontab -l 2>/dev/null | grep -E 'media-library-refresh|cinevault|thumbnail' || printf 'No CineVault cron entries found.\n'

section "Movie Library"
if [[ -f "$MOVIE_LIVE_CACHE" ]]; then
  python3 - "$MOVIE_LIVE_CACHE" <<'PY'
import json, pathlib, statistics, sys, time
path = pathlib.Path(sys.argv[1])
data = json.loads(path.read_text(encoding="utf-8"))
movies = data.get("movies", [])
sizes = [int(row.get("size") or 0) for row in movies]
def human(n):
    for unit in ["B","KB","MB","GB","TB"]:
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
total = sum(sizes)
print(f"Movie files in cache: {len(movies)}")
print(f"Total movie file size: {human(total)}")
if sizes:
    print(f"Average movie size: {human(total / len(sizes))}")
    print(f"Median movie size: {human(statistics.median(sizes))}")
buckets = [
    ("under 700 MB", lambda n: n < 700*1024**2),
    ("700 MB - 1.5 GB", lambda n: 700*1024**2 <= n < 1536*1024**2),
    ("1.5 GB - 3 GB", lambda n: 1536*1024**2 <= n < 3*1024**3),
    ("3 GB - 4 GB", lambda n: 3*1024**3 <= n < 4*1024**3),
    ("over 4 GB", lambda n: n >= 4*1024**3),
]
for label, pred in buckets:
    matched = [n for n in sizes if pred(n)]
    print(f"{label}: {len(matched)} files, {human(sum(matched))}")
ext = {}
for row in movies:
    suffix = pathlib.Path(row.get("rel_path") or row.get("path") or "").suffix.lower() or "(none)"
    ext[suffix] = ext.get(suffix, 0) + 1
print("Movie extensions:")
for key, value in sorted(ext.items(), key=lambda item: (-item[1], item[0])):
    print(f"  {key}: {value}")
recent = sorted(movies, key=lambda row: float(row.get("modified") or 0), reverse=True)[:10]
print("Recently modified movies:")
for row in recent:
    ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(float(row.get("modified") or 0)))
    print(f"  {ts} | {human(int(row.get('size') or 0))} | {row.get('title') or row.get('rel_path')}")
PY
else
  printf 'Movie live cache missing: %s\n' "$MOVIE_LIVE_CACHE"
  printf 'Movie video files by filesystem scan: %s\n' "$(count_files "$MOVIE_ROOT" "$VIDEO_EXT_REGEX")"
fi

section "TV Library"
if [[ -f "$TV_LIVE_CACHE" ]]; then
  python3 - "$TV_LIVE_CACHE" <<'PY'
import collections, json, pathlib, statistics, sys, time
path = pathlib.Path(sys.argv[1])
data = json.loads(path.read_text(encoding="utf-8"))
episodes = data.get("episodes", [])
sizes = [int(row.get("size") or 0) for row in episodes]
shows = collections.defaultdict(lambda: {"count":0, "size":0, "seasons":set()})
def human(n):
    for unit in ["B","KB","MB","GB","TB"]:
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
for row in episodes:
    show = row.get("show") or "Unknown"
    shows[show]["count"] += 1
    shows[show]["size"] += int(row.get("size") or 0)
    shows[show]["seasons"].add(row.get("season") or "Other")
print(f"TV shows: {len(shows)}")
print(f"TV episodes: {len(episodes)}")
print(f"Total TV episode file size: {human(sum(sizes))}")
if sizes:
    print(f"Average episode size: {human(sum(sizes) / len(sizes))}")
    print(f"Median episode size: {human(statistics.median(sizes))}")
buckets = [
    ("under 350 MB", lambda n: n < 350*1024**2),
    ("350 MB - 700 MB", lambda n: 350*1024**2 <= n < 700*1024**2),
    ("700 MB - 1.5 GB", lambda n: 700*1024**2 <= n < 1536*1024**2),
    ("1.5 GB - 3 GB", lambda n: 1536*1024**2 <= n < 3*1024**3),
    ("over 3 GB", lambda n: n >= 3*1024**3),
]
for label, pred in buckets:
    matched = [n for n in sizes if pred(n)]
    print(f"{label}: {len(matched)} episodes, {human(sum(matched))}")
ext = {}
for row in episodes:
    suffix = pathlib.Path(row.get("rel_path") or row.get("path") or "").suffix.lower() or "(none)"
    ext[suffix] = ext.get(suffix, 0) + 1
print("TV extensions:")
for key, value in sorted(ext.items(), key=lambda item: (-item[1], item[0])):
    print(f"  {key}: {value}")
print("Largest TV shows:")
for show, info in sorted(shows.items(), key=lambda item: item[1]["size"], reverse=True)[:20]:
    print(f"  {human(info['size'])} | {info['count']} episodes | {len(info['seasons'])} seasons | {show}")
recent = sorted(episodes, key=lambda row: float(row.get("modified") or 0), reverse=True)[:10]
print("Recently modified TV episodes:")
for row in recent:
    ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(float(row.get("modified") or 0)))
    print(f"  {ts} | {human(int(row.get('size') or 0))} | {row.get('show')} | {row.get('season')} | {row.get('title')}")
PY
else
  printf 'TV live cache missing: %s\n' "$TV_LIVE_CACHE"
  printf 'TV video files by filesystem scan: %s\n' "$(count_files "$TV_ROOT" "$VIDEO_EXT_REGEX")"
fi

section "Metadata, Posters, Thumbnails, Cache"
printf 'Movie posters: %s, files=%s\n' "$(human_size "$MOVIE_POSTER_DIR")" "$(count_files "$MOVIE_POSTER_DIR" '\.(jpg|jpeg|png|webp)$')"
printf 'TV posters: %s, files=%s\n' "$(human_size "$TV_POSTER_DIR")" "$(count_files "$TV_POSTER_DIR" '\.(jpg|jpeg|png|webp)$')"
printf 'Generated TV episode thumbnails: %s, files=%s\n' "$(human_size "$TV_EPISODE_THUMB_DIR")" "$(count_files "$TV_EPISODE_THUMB_DIR" '\.jpg$')"
printf 'Movie metadata map: %s\n' "$(human_size "$MOVIE_METADATA_MAP")"
printf 'TV metadata map: %s\n' "$(human_size "$TV_METADATA_MAP")"
printf 'Primary HLS cache: %s\n' "$(human_size "$HLS_CACHE_DIR")"
printf 'HLS lab cache: %s\n' "$(human_size "$HLS_LAB_CACHE_DIR")"

if [[ -f "$MOVIE_METADATA_MAP" || -f "$TV_METADATA_MAP" ]]; then
  python3 - "$MOVIE_METADATA_MAP" "$TV_METADATA_MAP" <<'PY'
import json, pathlib, sys
movie_path, tv_path = map(pathlib.Path, sys.argv[1:3])
def load(path):
    if not path.is_file(): return {}
    return json.loads(path.read_text(encoding="utf-8"))
movies = load(movie_path)
tv = load(tv_path)
movie_with_poster = sum(1 for row in movies.values() if row.get("poster_path") or row.get("backdrop_path"))
tv_episode_rows = 0
tv_episode_stills = 0
for row in tv.values():
    eps = row.get("episodes") or {}
    tv_episode_rows += len(eps)
    tv_episode_stills += sum(1 for ep in eps.values() if str(ep.get("still_path") or "").startswith("/"))
print(f"Movie metadata records: {len(movies)}")
print(f"Movie records with TMDB art paths: {movie_with_poster}")
print(f"TV metadata records: {len(tv)}")
print(f"TV episode metadata records: {tv_episode_rows}")
print(f"TV episode records with TMDB stills: {tv_episode_stills}")
PY
fi

section "Logs"
printf 'Refresh log directory size: %s\n' "$(human_size "$LOG_DIR")"
if [[ -d "$LOG_DIR" ]]; then
  printf 'Log files: %s\n' "$(find "$LOG_DIR" -type f | wc -l)"
  printf 'Compressed logs: %s\n' "$(find "$LOG_DIR" -type f -name '*.gz' | wc -l)"
  printf 'Logs over 100 MB not compressed: %s\n' "$(find "$LOG_DIR" -type f ! -name '*.gz' -size +100M | wc -l)"
  printf 'Newest logs:\n'
  { find "$LOG_DIR" -type f -printf '%T@ %p\n' 2>/dev/null | sort -nr | head -10 | cut -d' ' -f2-; } || true
fi

section "Comics"
printf 'Comics source: %s (%s)\n' "$COMICS_DIR" "$(human_size "$COMICS_DIR")"
printf 'Comic library web output: %s (%s)\n' "$COMIC_LIBRARY_DIR" "$(human_size "$COMIC_LIBRARY_DIR")"
printf 'Comic archive files: %s\n' "$(count_files "$COMICS_DIR" '\.(cbr|cbz|pdf|zip)$')"

section "Filesystem Free Space"
for path in "$MOVIE_ROOT" "$TV_ROOT" "$MOVIE_APP_DIR" "$TV_APP_DIR"; do
  if [[ -e "$path" ]]; then
    df -h "$path" | awk 'NR==1 || NR==2 {print}'
  fi
done | awk '!seen[$0]++'

section "Active ffmpeg / Transcode Processes"
pgrep -af 'ffmpeg|HandBrakeCLI|handbrake|transcode|orchestrator' || printf 'No matching ffmpeg/transcode processes found.\n'
