#!/usr/bin/env bash
set -euo pipefail

rm -f "/mnt/c/DATA/HANDBRAKE-H265/The SpongeBob Movie - Search for SquarePants (2025).h265.target-1GB.mp4"

for archive_dir in /mnt/nfs-share-movies/COMPLETED /mnt/nfs-share-tvshows/COMPLETED; do
  resolved="$(readlink -f "$archive_dir")"
  case "$resolved" in
    /mnt/nfs-share-movies/COMPLETED|/mnt/nfs-share-tvshows/COMPLETED)
      find "$resolved" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
      ;;
    *)
      echo "Refusing unexpected archive path: $resolved" >&2
      exit 1
      ;;
  esac
done

echo "Cleaned transcode completed archives."
