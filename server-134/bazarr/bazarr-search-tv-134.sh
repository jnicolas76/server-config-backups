#!/usr/bin/env bash
set -euo pipefail

bazarr_config=/home/jnicolas/bazarr/config/config.yaml
bazarr_api_key="$(sed -n '/^auth:/,/^[a-z]/p' "$bazarr_config" | sed -n 's/^  apikey: //p' | head -1)"
test "${#bazarr_api_key}" -eq 32

before="$(curl -fsS -H "X-API-KEY: $bazarr_api_key" 'http://127.0.0.1:6767/api/episodes/wanted?start=0&length=1')"
printf 'TV wanted before search: %s\n' "$(printf '%s' "$before" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("total"))')"

code="$(curl -sS -o /tmp/bazarr-tv-search-result -w '%{http_code}' -X PATCH \
  -H "X-API-KEY: $bazarr_api_key" \
  --data-urlencode action=search-wanted \
  http://127.0.0.1:6767/api/series)"
[[ "$code" == 204 ]] || { printf 'TV wanted search failed: HTTP %s\n' "$code" >&2; exit 1; }

after="$(curl -fsS -H "X-API-KEY: $bazarr_api_key" 'http://127.0.0.1:6767/api/episodes/wanted?start=0&length=1')"
printf 'TV wanted after search: %s\n' "$(printf '%s' "$after" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("total"))')"
curl -fsS -H "X-API-KEY: $bazarr_api_key" http://127.0.0.1:6767/api/providers
printf '\n'
