#!/usr/bin/env bash
set -u

BASE="/home/jnicolas"
CINEVAULT_PORT="${CINEVAULT_PORT:-8093}"
CINEVAULT_LAB_PORT="${CINEVAULT_LAB_PORT:-5000}"
CINEVAULT_CAST_PORT="${CINEVAULT_CAST_PORT:-8120}"
BOOKVAULT_PORT="${BOOKVAULT_PORT:-8112}"
COMICS_PORT="${COMICS_PORT:-8110}"
NES_PORT="${NES_PORT:-8092}"
SEGA_PORT="${SEGA_PORT:-8091}"
DOS_PORT="${DOS_PORT:-8101}"
MAME_PORT="${MAME_PORT:-8094}"
ARISTA_PORT="${ARISTA_PORT:-8095}"
LOST_PORT="${LOST_PORT:-8096}"
GAMEBOY_PORT="${GAMEBOY_PORT:-8097}"
GBA_PORT="${GBA_PORT:-8098}"
N64_PORT="${N64_PORT:-8099}"
PS1_PORT="${PS1_PORT:-8100}"
C64_PORT="${C64_PORT:-8102}"
ATARI2600_PORT="${ATARI2600_PORT:-8103}"
ATARI5200_PORT="${ATARI5200_PORT:-8104}"
ATARI7800_PORT="${ATARI7800_PORT:-8105}"

log() {
  printf '[%s] %s\n' "$(date '+%F %T')" "$*"
}

kill_pidfile() {
  local name="$1"
  local pidfile="$2"
  if [[ -f "$pidfile" ]]; then
    local pid
    pid="$(cat "$pidfile" 2>/dev/null || true)"
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      log "Stopping $name PID $pid"
      kill "$pid" 2>/dev/null || true
      sleep 2
      if kill -0 "$pid" 2>/dev/null; then
        log "Force stopping $name PID $pid"
        kill -9 "$pid" 2>/dev/null || true
      fi
    else
      log "$name pidfile exists but process is not running"
    fi
    rm -f "$pidfile"
  else
    log "$name pidfile not found"
  fi
}

kill_pattern() {
  local name="$1"
  local pattern="$2"
  local pids
  pids="$(pgrep -f "$pattern" 2>/dev/null || true)"
  if [[ -z "$pids" ]]; then
    log "$name not running"
    return 0
  fi
  log "Stopping $name PID(s): $pids"
  kill $pids 2>/dev/null || true
  sleep 2
  pids="$(pgrep -f "$pattern" 2>/dev/null || true)"
  if [[ -n "$pids" ]]; then
    log "Force stopping $name PID(s): $pids"
    kill -9 $pids 2>/dev/null || true
  fi
}

log "Stopping CineVault stack on $(hostname)"

kill_pidfile "CineVault" "$BASE/cinevault-8093.pid"
kill_pattern "CineVault fallback" "$BASE/cinevault-watch-8093.py"

if [[ -x "$BASE/cinemediavault-lab/stop_lab_5000.sh" ]]; then
  log "Stopping CineMediaVault Lab with wrapper"
  "$BASE/cinemediavault-lab/stop_lab_5000.sh" || true
else
  kill_pattern "CineMediaVault Lab fallback" "$BASE/cinemediavault-lab/cinemediavault-lab-5000.py"
fi

kill_pattern "CineVault cast controller" "$BASE/cinevault_cast_controller.py"

kill_pidfile "BookVault" "$BASE/bookvault/bookvault-8112.pid"
kill_pattern "BookVault fallback" "$BASE/bookvault/bookvault_server.py"

kill_pidfile "Comic Library" "/media/jnicolas/Expansion/comic-library/.server.pid"
kill_pattern "Comic Library fallback" "python3 -m http.server $COMICS_PORT"

for pidfile in "$BASE"/software/*/.server.pid; do
  [[ -f "$pidfile" ]] || continue
  game_name="$(basename "$(dirname "$pidfile")")"
  kill_pidfile "Game library $game_name" "$pidfile"
done

# Catch any game launchers that did not leave PID files.
kill_pattern "remaining game library servers" "$BASE/software/.*/scripts/serve.py"

log "Remaining known service ports:"
ss -ltnp 2>/dev/null | grep -E ':(5000|8091|8092|8093|8094|8095|8096|8097|8098|8099|8100|8101|8102|8103|8104|8105|8110|8112|8120)' || true

log "Done."
