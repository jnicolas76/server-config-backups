#!/usr/bin/env bash
set -euo pipefail
bazarr_config=/home/jnicolas/bazarr/config/config.yaml
bazarr_api_key="$(sed -n '/^auth:/,/^[a-z]/p' "$bazarr_config" | sed -n 's/^  apikey: //p' | head -1)"
for endpoint in providers 'episodes/wanted?start=0&length=1' 'movies/wanted?start=0&length=1'; do
  printf '%s ' "$endpoint"
  curl -fsS -H "X-API-KEY: $bazarr_api_key" "http://127.0.0.1:6767/api/$endpoint"
  printf '\n'
done
