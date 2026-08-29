#!/usr/bin/env bash
set -euo pipefail

bazarr_config=/home/jnicolas/bazarr/config/config.yaml
bazarr_api_key="$(sed -n '/^auth:/,/^[a-z]/p' "$bazarr_config" | sed -n 's/^  apikey: //p' | head -1)"
test "${#bazarr_api_key}" -eq 32

mapfile -t movie_ids < <(docker exec bazarr python3 -c "import sqlite3; d=sqlite3.connect('/config/db/bazarr.db'); print(*[r[0] for r in d.execute('select radarrId from table_movies where profileId is null')], sep='\\n')")
mapfile -t series_ids < <(docker exec bazarr python3 -c "import sqlite3; d=sqlite3.connect('/config/db/bazarr.db'); print(*[r[0] for r in d.execute('select sonarrSeriesId from table_shows where profileId is null')], sep='\\n')")

assign_batch() {
  local endpoint="$1" id_name="$2"
  shift 2
  local args=() id
  for id in "$@"; do
    [[ -n "$id" ]] || continue
    args+=(--data-urlencode "$id_name=$id" --data-urlencode profileid=1)
  done
  ((${#args[@]})) || return 0
  local code
  code="$(curl -sS -o /tmp/bazarr-profile-assign-result -w '%{http_code}' -X POST \
    -H "X-API-KEY: $bazarr_api_key" "${args[@]}" "http://127.0.0.1:6767/api/$endpoint")"
  [[ "$code" == 204 ]] || { printf '%s assignment failed: HTTP %s\n' "$endpoint" "$code" >&2; return 1; }
}

batch_size=25
for ((offset=0; offset<${#movie_ids[@]}; offset+=batch_size)); do
  assign_batch movies radarrid "${movie_ids[@]:offset:batch_size}"
  printf 'Movie profiles assigned: %d/%d\n' "$(( offset + batch_size > ${#movie_ids[@]} ? ${#movie_ids[@]} : offset + batch_size ))" "${#movie_ids[@]}"
done

for ((offset=0; offset<${#series_ids[@]}; offset+=batch_size)); do
  assign_batch series seriesid "${series_ids[@]:offset:batch_size}"
  printf 'Series profiles assigned: %d/%d\n' "$(( offset + batch_size > ${#series_ids[@]} ? ${#series_ids[@]} : offset + batch_size ))" "${#series_ids[@]}"
done

printf 'Profile assignment complete: %d movies, %d series.\n' "${#movie_ids[@]}" "${#series_ids[@]}"
