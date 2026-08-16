#!/usr/bin/env bash
set -euo pipefail

for dir in /mnt/nfs-share-tvshows/COMPLETED /mnt/nfs-share-movies/COMPLETED; do
  resolved="$(readlink -f "$dir")"
  case "$resolved" in
    /mnt/nfs-share-tvshows/COMPLETED|/mnt/nfs-share-movies/COMPLETED)
      ;;
    *)
      echo "Refusing unsafe path: $dir -> $resolved" >&2
      exit 1
      ;;
  esac

  echo "Cleaning $resolved"
  find "$resolved" -type f -print -delete
  find "$resolved" -depth -type d -empty ! -path "$resolved" -delete
done
