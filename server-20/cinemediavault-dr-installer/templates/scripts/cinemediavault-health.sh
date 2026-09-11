#!/usr/bin/env bash
# CineMediaVault health check and bounded self-heal.
# GENERATED FILE - regenerate with `cinevaultctl apply`.
#
# Design notes:
#  * Two consecutive failures are required before a restart. A single stalled
#    probe on a busy server is normal; restarting on one would interrupt
#    playback for a transient blip and mask real failures.
#  * A restart cooldown prevents a restart storm when the service dies again
#    immediately - for example after a bad configuration change. Further
#    failures are logged but suppressed until the cooldown elapses.
#  * The probe is bounded on both connect and total time so one check can never
#    overrun into the next.
set -u

URL="{{ health_url }}"
STATE_DIR="{{ state_root }}/health"
LOG="{{ log_root }}/health.log"
UNIT="cinemediavault.service"

FAILURE_THRESHOLD="${CINEVAULT_HEALTH_FAILURE_THRESHOLD:-{{ failure_threshold }}}"
CONNECT_TIMEOUT="${CINEVAULT_HEALTH_CONNECT_TIMEOUT:-3}"
MAX_TIME="${CINEVAULT_HEALTH_MAX_TIME:-10}"
RESTART_COOLDOWN="${CINEVAULT_HEALTH_RESTART_COOLDOWN:-{{ restart_cooldown }}}"
DISK_WARN_PERCENT="${CINEVAULT_DISK_WARN_PERCENT:-85}"
DISK_CRIT_PERCENT="${CINEVAULT_DISK_CRIT_PERCENT:-93}"

mkdir -p "$STATE_DIR" "$(dirname "$LOG")"
FAILURE_FILE="$STATE_DIR/consecutive-failures"
RESTART_FILE="$STATE_DIR/last-restart"
DISK_FILE="$STATE_DIR/last-disk-state"

log() { printf '%s %s\n' "$(date -Is)" "$*" >>"$LOG"; }

now=$(date +%s)

probe() {
  curl -k -fsS --connect-timeout "$CONNECT_TIMEOUT" --max-time "$MAX_TIME" \
    -o /dev/null "$URL" 2>/dev/null
}

# --- disk pressure ---------------------------------------------------------
used=$(df --output=pcent "{{ state_root }}" 2>/dev/null | tail -1 | tr -dc '0-9')
if [ -n "${used:-}" ]; then
  previous=$(cat "$DISK_FILE" 2>/dev/null || echo ok)
  if [ "$used" -ge "$DISK_CRIT_PERCENT" ]; then
    [ "$previous" = critical ] || log "CRITICAL: state volume ${used}% used"
    echo critical >"$DISK_FILE"
  elif [ "$used" -ge "$DISK_WARN_PERCENT" ]; then
    [ "$previous" = warning ] || log "WARNING: state volume ${used}% used"
    echo warning >"$DISK_FILE"
  else
    [ "$previous" = ok ] || log "state volume back to ${used}% used"
    echo ok >"$DISK_FILE"
  fi
fi

# --- health probe ------------------------------------------------------------
if probe; then
  if [ -s "$FAILURE_FILE" ] && [ "$(cat "$FAILURE_FILE")" != "0" ]; then
    log "recovered after $(cat "$FAILURE_FILE") failed probe(s)"
  fi
  echo 0 >"$FAILURE_FILE"
  exit 0
fi

failures=$(cat "$FAILURE_FILE" 2>/dev/null || echo 0)
failures=$((failures + 1))
echo "$failures" >"$FAILURE_FILE"
log "health probe failed ($failures/$FAILURE_THRESHOLD): $URL"

if [ "$failures" -lt "$FAILURE_THRESHOLD" ]; then
  exit 1
fi

last_restart=$(cat "$RESTART_FILE" 2>/dev/null || echo 0)
if [ $((now - last_restart)) -lt "$RESTART_COOLDOWN" ]; then
  log "restart suppressed: last restart was $((now - last_restart))s ago (cooldown ${RESTART_COOLDOWN}s)"
  exit 1
fi

log "restarting $UNIT after $failures consecutive failures"
echo "$now" >"$RESTART_FILE"
echo 0 >"$FAILURE_FILE"
if systemctl restart "$UNIT"; then
  log "restart issued"
else
  log "restart FAILED; investigate with: systemctl status $UNIT"
fi
exit 1
