#!/usr/bin/env bash
# CineMediaVault library refresh.
# GENERATED FILE - regenerate with `cinevaultctl apply`.
#
# Rebuilds the movie and TV indexes without touching a single media file. The
# mount guard is the important part: an unavailable share looks exactly like an
# empty library to a directory walk, and writing that result would erase every
# title from the catalogue.
set -uo pipefail

LOG="{{ log_root }}/refresh.log"
LOCK="{{ state_root }}/refresh.lock"
mkdir -p "$(dirname "$LOG")"

log() { printf '%s %s\n' "$(date -Is)" "$*" | tee -a "$LOG"; }

exec 9>"$LOCK"
if ! flock -n 9; then
  log "another refresh is already running; exiting"
  exit 0
fi

status=0

check_root() {
  # $1 = label, $2 = path
  local label="$1" path="$2"
  [ -n "$path" ] || return 1
  if [ ! -d "$path" ]; then
    log "SKIP $label: $path does not exist"
    return 1
  fi
  if [ -z "$(ls -A "$path" 2>/dev/null)" ]; then
    log "SKIP $label: $path is empty. Refusing to rebuild the index from an" \
        "empty directory - an unmounted share would wipe the catalogue."
    return 1
  fi
  return 0
}

{% if movies %}
if check_root "movies" "${MOVIE_ROOT:-}"; then
  log "refreshing movie index"
  if /usr/bin/python3 "{{ scripts_dir }}/refresh_library_index.py" --app-dir "{{ app_dir }}" movies >>"$LOG" 2>&1; then
    log "movie index refreshed"
  else
    log "movie index refresh FAILED"
    status=1
  fi
fi
{% endif %}

{% if tv %}
if check_root "tv" "${TV_ROOT:-}"; then
  log "refreshing TV index"
  if /usr/bin/python3 "{{ scripts_dir }}/refresh_library_index.py" --app-dir "{{ app_dir }}" tv >>"$LOG" 2>&1; then
    log "TV index refreshed"
  else
    log "TV index refresh FAILED"
    status=1
  fi
fi
{% endif %}

log "refresh finished (status $status)"
exit "$status"
