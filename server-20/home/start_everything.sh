#!/usr/bin/env bash
set -u

BASE="/home/jnicolas"
LOG_DIR="$BASE/startup-logs"
mkdir -p "$LOG_DIR"

# Base ports. Override any of these before running the script, for example:
# CINEVAULT_PORT=8093 NES_PORT=8092 ./start_everything.sh
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

port_listening() {
  local port="$1"
  ss -ltn 2>/dev/null | awk '{print $4}' | grep -Eq "(:|\\])${port}$"
}

start_cmd() {
  local name="$1"
  local port="$2"
  local pidfile="$3"
  local logfile="$4"
  shift 4

  if port_listening "$port"; then
    log "$name already listening on port $port"
    return 0
  fi

  log "Starting $name on port $port"
  nohup "$@" >>"$logfile" 2>&1 < /dev/null &
  echo "$!" > "$pidfile"
  sleep 2

  if port_listening "$port"; then
    log "$name started as PID $(cat "$pidfile")"
  else
    log "WARNING: $name did not open port $port. Check $logfile"
  fi
}

start_script_bg() {
  local name="$1"
  local port="$2"
  local script="$3"
  local logfile="$4"

  if port_listening "$port"; then
    log "$name already listening on port $port"
    return 0
  fi

  if [[ ! -x "$script" ]]; then
    log "Skipping $name: missing executable $script"
    return 0
  fi

  log "Starting $name with $script"
  nohup "$script" >>"$logfile" 2>&1 < /dev/null &
  sleep 2

  if port_listening "$port"; then
    log "$name started on port $port"
  else
    log "WARNING: $name did not open port $port. Check $logfile"
  fi
}

start_script_fg_wrapper() {
  local name="$1"
  local port="$2"
  local dir="$3"
  local script="$4"
  local logfile="$5"
  local pidfile="$6"

  if port_listening "$port"; then
    log "$name already listening on port $port"
    return 0
  fi

  if [[ ! -x "$dir/$script" ]]; then
    log "Skipping $name: missing executable $dir/$script"
    return 0
  fi

  log "Starting $name on port $port"
  (
    cd "$dir" || exit 1
    nohup "./$script" "$port" >>"$logfile" 2>&1 < /dev/null &
    echo "$!" > "$pidfile"
  )
  sleep 2

  if port_listening "$port"; then
    log "$name started as PID $(cat "$pidfile" 2>/dev/null || true)"
  else
    log "WARNING: $name did not open port $port. Check $logfile"
  fi
}

log "Starting CineVault stack on $(hostname)"

# CineVault combined Movies/TV/Comics front-end. Includes direct/HLS playback.
start_cmd "CineVault" "$CINEVAULT_PORT" "$BASE/cinevault-8093.pid" "$BASE/cinevault-8093.log" \
  python3 "$BASE/cinevault-watch-8093.py"

# CineMediaVault lab instance. Uses its own wrapper so TLS/env/db/cache stay isolated.
start_script_bg "CineMediaVault Lab" "$CINEVAULT_LAB_PORT" "$BASE/cinemediavault-lab/start_lab_5000.sh" \
  "$LOG_DIR/cinemediavault-lab-5000.log"

# Server-side cast discovery/controller layer.
start_script_bg "CineVault Cast Controller" "$CINEVAULT_CAST_PORT" "$BASE/start_cinevault_cast_controller.sh" \
  "$LOG_DIR/cinevault-cast-controller.log"

# BookVault.
start_script_bg "BookVault" "$BOOKVAULT_PORT" "$BASE/bookvault/start_bookvault_8112.sh" \
  "$LOG_DIR/bookvault-8112.log"

# Comic library static host.
start_script_fg_wrapper "Comic Library" "$COMICS_PORT" "/media/jnicolas/Expansion/comic-library" "start_library.sh" \
  "/media/jnicolas/Expansion/comic-library/server.log" "/media/jnicolas/Expansion/comic-library/.server.pid"

# Game libraries. The port variables above are passed to start.sh when present.
declare -A GAME_PORTS=(
  [NES]="$NES_PORT" [SEGA]="$SEGA_PORT" [DOS]="$DOS_PORT" [MAME]="$MAME_PORT"
  [ARISTA]="$ARISTA_PORT" [LOST]="$LOST_PORT" [GAMEBOY]="$GAMEBOY_PORT" [GBA]="$GBA_PORT"
  [N64]="$N64_PORT" [PS1]="$PS1_PORT" [C64]="$C64_PORT"
  [ATARI2600]="$ATARI2600_PORT" [ATARI5200]="$ATARI5200_PORT" [ATARI7800]="$ATARI7800_PORT"
)
for game_name in "${!GAME_PORTS[@]}"; do
  game_script="$BASE/software/$game_name/start.sh"
  [[ -x "$game_script" ]] || continue
  start_script_bg "Game library $game_name" "${GAME_PORTS[$game_name]}" "$game_script" "$LOG_DIR/game-${game_name}.log"
done

log "Current known service ports:"
ss -ltnp 2>/dev/null | grep -E ':8091|:8092|:8093|:8094|:8101|:8110|:8112|:8120|:8080|:5000' || true

log "Done."
