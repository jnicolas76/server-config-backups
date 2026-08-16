#!/usr/bin/env bash
set -euo pipefail

SOURCE="/mnt/nfs-share-movies/Movies/Allied (2016)/Allied (2016) Bluray-2160p.mkv"
WORKER="/mnt/c/DATA/handbrake_transcode_worker.py"
WORK_DIR="/mnt/c/DATA/HANDBRAKE"
COMPLETED_DIR="/mnt/nfs-share-movies/COMPLETED"
LOG_FILE="/mnt/c/DATA/handbrake-movies-1.5gb.log"

common_args=(
  --file "$SOURCE"
  --work-dir "$WORK_DIR"
  --target-gb 1.5
  --encoder x264
  --preset slow
  --movie-root /mnt/nfs-share-movies/Movies
  --completed-dir "$COMPLETED_DIR"
  --library-csv /mnt/c/DATA/movie-file-sizes.csv
  --library-html /mnt/c/DATA/movie-file-sizes.html
  --html-generator /mnt/c/DATA/movie_csv_to_html.py
  --log-file "$LOG_FILE"
)

python3 "$WORKER" "${common_args[@]}"
python3 "$WORKER" "${common_args[@]}" --replace-completed

encoded="/mnt/nfs-share-movies/Movies/Allied (2016)/Allied (2016) Bluray-2160p.mp4"
clean="/mnt/nfs-share-movies/Movies/Allied (2016)/Allied (2016).mp4"
if [[ -f "$encoded" && ! -e "$clean" ]]; then
  mv -- "$encoded" "$clean"
fi
