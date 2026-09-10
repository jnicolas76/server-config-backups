#!/usr/bin/env bash
set -u

LAB=/home/jnicolas/cinemediavault-lab
URL=https://127.0.0.1:5000/login
STATE="$LAB/.healthcheck-failures"
LOG="$LAB/logs/healthcheck.log"
TIMING_LOG="$LAB/logs/healthcheck-timing.log"
RESTART_STATE="$LAB/.healthcheck-last-restart"

# ===== Adjustable settings (documented) =====
# Consecutive failed probes required before we attempt a restart. Keeping
# this at 2 means a single isolated stall (see below) never triggers a
# restart on its own -- only sustained unavailability does. Do not lower
# this to 1: that would restart the service on ordinary transient network
# blips and weaken genuine failure detection.
FAILURE_THRESHOLD="${CINEVAULT_HEALTHCHECK_FAILURE_THRESHOLD:-2}"
# Bounded, lightweight probe: fails fast on a dead TCP path (connect-timeout)
# and gives up quickly even if something is unusually slow (max-time), so a
# single check can never block the next cron tick (cron runs this once a
# minute under flock).
CONNECT_TIMEOUT_SECONDS="${CINEVAULT_HEALTHCHECK_CONNECT_TIMEOUT:-2}"
MAX_TIME_SECONDS="${CINEVAULT_HEALTHCHECK_MAX_TIME:-8}"
# Minimum seconds between two restarts triggered by this script. Prevents a
# restart storm if the app dies again immediately after being restarted
# (e.g. a bad deploy): once we've just restarted it, further failures are
# logged but suppressed until this cooldown elapses instead of looping.
RESTART_COOLDOWN_SECONDS="${CINEVAULT_HEALTHCHECK_RESTART_COOLDOWN:-300}"

# Root disk monitoring thresholds (percent used). Root ("/") is the
# filesystem that also backs /home and therefore the CineVault DB, logs,
# posters, thumbnails, and HLS/subtitle caches on this host. Verified
# baseline at the time these thresholds were chosen: ~74% used (640G/916G).
# WARNING gives an early signal well before the disk actually fills;
# CRITICAL is close enough to full that DB writes/log rotation/HLS
# transcodes could start failing soon. Override via env only if the real
# baseline usage changes; do not silence by raising these past 95.
DISK_WARN_PERCENT="${CINEVAULT_DISK_WARN_PERCENT:-80}"
DISK_CRIT_PERCENT="${CINEVAULT_DISK_CRIT_PERCENT:-90}"
DISK_STATE="$LAB/.healthcheck-disk-state"
# Low-noise re-alert interval while sustained in WARNING/CRITICAL, so a full
# disk doesn't get silently forgotten but also doesn't spam the log once a
# minute for hours.
DISK_REALERT_SECONDS="${CINEVAULT_DISK_REALERT_SECONDS:-3600}"
# ===============================

mkdir -p "$(dirname "$LOG")"

log() {
  printf '%s %s\n' "$(date --iso-8601=seconds)" "$*" >> "$LOG"
}

check_disk() {
  # "df -P /" is POSIX-format, single filesystem line, safe to parse.
  local line percent prev_state prev_ts now state message
  line="$(df -P / 2>/dev/null | tail -n1)"
  percent="$(printf '%s' "$line" | awk '{gsub("%","",$5); print $5}')"
  [[ "$percent" =~ ^[0-9]+$ ]] || return 0

  if (( percent >= DISK_CRIT_PERCENT )); then
    state="CRITICAL"
  elif (( percent >= DISK_WARN_PERCENT )); then
    state="WARNING"
  else
    state="OK"
  fi

  prev_state="OK"
  prev_ts=0
  if [[ -f "$DISK_STATE" ]]; then
    read -r prev_state prev_ts < "$DISK_STATE" 2>/dev/null || true
  fi
  now="$(date +%s)"

  if [[ "$state" == "OK" ]]; then
    if [[ "$prev_state" != "OK" ]]; then
      log "disk usage recovered: ${percent}% used on / (below warning threshold ${DISK_WARN_PERCENT}%)"
    fi
    rm -f "$DISK_STATE"
    return 0
  fi

  # Log on every state transition, and otherwise at most once per
  # DISK_REALERT_SECONDS while sustained -- this is the low-noise behavior.
  if [[ "$state" != "$prev_state" ]] || (( now - prev_ts >= DISK_REALERT_SECONDS )); then
    message="disk usage ${state}: ${percent}% used on / (warn=${DISK_WARN_PERCENT}%, crit=${DISK_CRIT_PERCENT}%) -- ${line}"
    log "$message"
    # Store the time of the last emitted alert, not the time of every check.
    # Updating this unconditionally would prevent the hourly re-alert window
    # from ever being reached while the state remains unchanged.
    printf '%s %s\n' "$state" "$now" > "$DISK_STATE"
  fi
}

probe() {
  # -w emits machine-readable timing/result fields even on failure output
  # capture, giving concrete timing evidence for diagnosing isolated stalls
  # without needing a second, heavier request.
  curl -k -sS \
    --connect-timeout "$CONNECT_TIMEOUT_SECONDS" \
    --max-time "$MAX_TIME_SECONDS" \
    -o /dev/null \
    -w '%{http_code} %{time_connect} %{time_appconnect} %{time_starttransfer} %{time_total}' \
    "$URL" 2>/tmp/cinevault-healthcheck-curl-err.$$
  echo " exit=$?"
}

check_disk

probe_output="$(probe)"
probe_exit="$(printf '%s' "$probe_output" | grep -o 'exit=[0-9-]*' | cut -d= -f2)"
http_code="$(printf '%s' "$probe_output" | awk '{print $1}')"

# Timing evidence: one compact line per run, always, in a dedicated
# low-priority log so the primary healthcheck.log stays limited to actual
# failures/restarts. This is what lets an isolated stall be correlated
# against slow response times after the fact instead of only ever seeing a
# bare "failed" line.
printf '%s %s\n' "$(date --iso-8601=seconds)" "$probe_output" >> "$TIMING_LOG"

if [[ "$probe_exit" == "0" && "$http_code" =~ ^[23][0-9][0-9]$ ]]; then
  rm -f "$STATE"
  rm -f /tmp/cinevault-healthcheck-curl-err.$$ 2>/dev/null
  exit 0
fi

curl_err=""
if [[ -f /tmp/cinevault-healthcheck-curl-err.$$ ]]; then
  curl_err="$(tail -c 300 /tmp/cinevault-healthcheck-curl-err.$$ 2>/dev/null)"
  rm -f /tmp/cinevault-healthcheck-curl-err.$$ 2>/dev/null
fi

failures=0
[[ -f "$STATE" ]] && read -r failures < "$STATE"
failures=$((failures + 1))
printf '%s\n' "$failures" > "$STATE"
log "health check failed (${failures}/${FAILURE_THRESHOLD}) http_code=${http_code:-none} timing='${probe_output}' curl_err='${curl_err}'"

if (( failures >= FAILURE_THRESHOLD )); then
  last_restart=0
  [[ -f "$RESTART_STATE" ]] && read -r last_restart < "$RESTART_STATE" 2>/dev/null || true
  now="$(date +%s)"
  since_last=$(( now - last_restart ))
  if (( since_last < RESTART_COOLDOWN_SECONDS )); then
    log "restart suppressed: cooldown active (${since_last}s since last restart, cooldown=${RESTART_COOLDOWN_SECONDS}s) -- avoiding a restart storm"
    # Do not clear STATE here: keep counting so we still notice if the
    # outage continues past the cooldown window.
    exit 0
  fi
  log "restarting unresponsive CineVault Lab"
  printf '%s\n' "$now" > "$RESTART_STATE"
  "$LAB/start_lab_5000.sh" >> "$LOG" 2>&1
  rm -f "$STATE"
fi
