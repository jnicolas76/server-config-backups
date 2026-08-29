#!/usr/bin/env bash
set -euo pipefail

bazarr_config=/home/jnicolas/bazarr/config/config.yaml
radarr_config=/home/jnicolas/config/radarr/config.xml
sonarr_config=/home/jnicolas/config/sonarr/config.xml

bazarr_api_key="$(sed -n '/^auth:/,/^[a-z]/p' "$bazarr_config" | sed -n 's/^  apikey: //p' | head -1)"
radarr_api_key="$(sed -n 's:.*<ApiKey>\(.*\)</ApiKey>.*:\1:p' "$radarr_config")"
sonarr_api_key="$(sed -n 's:.*<ApiKey>\(.*\)</ApiKey>.*:\1:p' "$sonarr_config")"

test "${#bazarr_api_key}" -eq 32
test "${#radarr_api_key}" -eq 32
test "${#sonarr_api_key}" -eq 32

http_code="$(curl -sS -o /tmp/bazarr-settings-result -w '%{http_code}' -X POST \
  -H "X-API-KEY: $bazarr_api_key" \
  --data-urlencode settings-general-use_radarr=true \
  --data-urlencode settings-radarr-ip=radarr \
  --data-urlencode settings-radarr-port=7878 \
  --data-urlencode settings-radarr-base_url=/ \
  --data-urlencode settings-radarr-ssl=false \
  --data-urlencode "settings-radarr-apikey=$radarr_api_key" \
  --data-urlencode settings-general-use_sonarr=true \
  --data-urlencode settings-sonarr-ip=sonarr \
  --data-urlencode settings-sonarr-port=8989 \
  --data-urlencode settings-sonarr-base_url=/ \
  --data-urlencode settings-sonarr-ssl=false \
  --data-urlencode "settings-sonarr-apikey=$sonarr_api_key" \
  http://127.0.0.1:6767/api/system/settings)"

if [[ "$http_code" != 204 ]]; then
  printf 'Bazarr settings API returned HTTP %s\n' "$http_code" >&2
  sed -E 's/[A-Fa-f0-9]{32}/REDACTED/g' /tmp/bazarr-settings-result >&2
  exit 1
fi

printf 'Bazarr Radarr/Sonarr settings accepted (HTTP %s).\n' "$http_code"

profile_json='[{"profileId":1,"name":"CineVault English + Spanish","cutoff":null,"items":[{"id":1,"language":"en","hi":"False","forced":"False","audio_exclude":"False","audio_only_include":"False"},{"id":2,"language":"es","hi":"False","forced":"False","audio_exclude":"False","audio_only_include":"False"}],"mustContain":[],"mustNotContain":[],"originalFormat":false,"tag":null}]'

http_code="$(curl -sS -o /tmp/bazarr-profile-result -w '%{http_code}' -X POST \
  -H "X-API-KEY: $bazarr_api_key" \
  --data-urlencode languages-enabled=en \
  --data-urlencode languages-enabled=es \
  --data-urlencode "languages-profiles=$profile_json" \
  --data-urlencode settings-general-movie_default_enabled=true \
  --data-urlencode settings-general-movie_default_profile=1 \
  --data-urlencode settings-general-serie_default_enabled=true \
  --data-urlencode settings-general-serie_default_profile=1 \
  --data-urlencode settings-general-enabled_providers=tvsubtitles \
  --data-urlencode settings-general-enabled_providers=gestdown \
  --data-urlencode settings-general-adaptive_searching=true \
  --data-urlencode settings-general-use_embedded_subs=true \
  --data-urlencode settings-general-upgrade_subs=false \
  --data-urlencode settings-general-upgrade_manual=false \
  --data-urlencode settings-general-minimum_score=90 \
  --data-urlencode settings-general-minimum_score_movie=75 \
  http://127.0.0.1:6767/api/system/settings)"

if [[ "$http_code" != 204 ]]; then
  printf 'Bazarr profile API returned HTTP %s\n' "$http_code" >&2
  cat /tmp/bazarr-profile-result >&2
  exit 1
fi

printf 'Bazarr English/Spanish profile and credential-free TV providers accepted (HTTP %s).\n' "$http_code"
