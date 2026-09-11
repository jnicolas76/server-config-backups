#!/usr/bin/env bash
#
# CineMediaVault - single bootstrap entry point.
#
#   sudo ./install.sh                 install the prerequisites and open the
#                                     setup website in your browser
#   sudo ./install.sh --config f.yaml  unattended install from a file
#   sudo ./install.sh --dry-run        check this machine, change nothing
#
# This script does as little as possible: it verifies the machine can run the
# installer, installs only the handful of packages the installer itself needs,
# and then hands over to the Python engine. Everything that decides *what* gets
# installed lives there, so the browser wizard and an unattended file take
# exactly the same path.
#
set -euo pipefail

PACKAGE_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd -P)"
readonly PACKAGE_ROOT

# Only what the installer engine and the setup website themselves need. The
# engine installs the rest based on what you actually select.
readonly BOOTSTRAP_PACKAGES=(python3 ca-certificates curl openssl)

readonly MIN_PYTHON_MAJOR=3
readonly MIN_PYTHON_MINOR=10

SETUP_PORT="${CMV_SETUP_PORT:-8099}"
SETUP_HOST=""
CONFIG_FILE=""
DRY_RUN=0
ASSUME_YES=0
NO_BROWSER=0
OFFLINE=0
EXTRA_ARGS=()

# ---------------------------------------------------------------- output ----

if [[ -t 1 ]] && [[ -z "${NO_COLOR:-}" ]]; then
  C_RESET=$'\033[0m'; C_BOLD=$'\033[1m'; C_DIM=$'\033[2m'
  C_RED=$'\033[38;5;203m'; C_GREEN=$'\033[38;5;35m'
  C_YELLOW=$'\033[38;5;214m'; C_BLUE=$'\033[38;5;75m'
else
  C_RESET=""; C_BOLD=""; C_DIM=""; C_RED=""; C_GREEN=""; C_YELLOW=""; C_BLUE=""
fi

info()  { printf '%s\n' "  $*"; }
step()  { printf '%s\n' "${C_BLUE}::${C_RESET} $*"; }
ok()    { printf '%s\n' "${C_GREEN}OK${C_RESET} $*"; }
warn()  { printf '%s\n' "${C_YELLOW}!!${C_RESET} $*" >&2; }
die()   { printf '%s\n' "${C_RED}XX${C_RESET} $*" >&2; exit 1; }

banner() {
  cat <<'ART'

   ____ _            __  __          _ _    __     __         _ _
  / ___(_)_ __   ___|  \/  | ___  __| (_) __\ \   / /_ _ _   _| | |_
 | |   | | '_ \ / _ \ |\/| |/ _ \/ _` | |/ _ \ \ / / _` | | | | | __|
 | |___| | | | |  __/ |  | |  __/ (_| | | (_| \ V / (_| | |_| | | |_
  \____|_|_| |_|\___|_|  |_|\___|\__,_|_|\__,_|\_/ \__,_|\__,_|_|\__|

ART
}

usage() {
  cat <<'USAGE'
Usage: sudo ./install.sh [options]

  Without a configuration file, this starts a setup website on this machine and
  prints the address and a one-time setup code to enter.

Options:
  --config FILE      install unattended from a configuration file
  --dry-run          check everything and print the plan; change nothing
  --port PORT        port for the setup website (default 8099)
  --host ADDRESS     address for the setup website (default: this machine's
                     LAN address, falling back to 127.0.0.1). Must be private.
  --no-browser       do not try to open a browser
  --offline          make no outbound network requests
  --yes              do not prompt for confirmation
  --help             show this message

Examples:
  sudo ./install.sh
  sudo ./install.sh --dry-run
  sudo ./install.sh --config /root/cinemediavault.yaml
  ./install.sh --dry-run --config example.yaml     (no root needed for a check)

After installation, manage the server with:
  sudo cinevaultctl status
  sudo cinevaultctl smoke-test
  sudo cinevaultctl backup
USAGE
}

# ------------------------------------------------------------- arguments ----

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config)     CONFIG_FILE="${2:-}"; shift 2 ;;
    --config=*)   CONFIG_FILE="${1#*=}"; shift ;;
    --port)       SETUP_PORT="${2:-}"; shift 2 ;;
    --port=*)     SETUP_PORT="${1#*=}"; shift ;;
    --host)       SETUP_HOST="${2:-}"; shift 2 ;;
    --host=*)     SETUP_HOST="${1#*=}"; shift ;;
    --dry-run)    DRY_RUN=1; shift ;;
    --no-browser) NO_BROWSER=1; shift ;;
    --offline)    OFFLINE=1; shift ;;
    --yes|-y)     ASSUME_YES=1; shift ;;
    --help|-h)    usage; exit 0 ;;
    --)           shift; EXTRA_ARGS+=("$@"); break ;;
    *)            die "unknown option: $1 (try --help)" ;;
  esac
done

if ! [[ "$SETUP_PORT" =~ ^[0-9]+$ ]] || (( SETUP_PORT < 1024 || SETUP_PORT > 65535 )); then
  die "--port must be a number between 1024 and 65535"
fi

# ------------------------------------------------------------- preflight ----

require_root() {
  if [[ "$(id -u)" -ne 0 ]]; then
    die "This installer must run as root.
     Re-run it with:  sudo ./install.sh $*"
  fi
}

check_os() {
  [[ -r /etc/os-release ]] || die "cannot read /etc/os-release; this installer targets Ubuntu"
  # shellcheck disable=SC1091
  . /etc/os-release
  case "${ID:-}" in
    ubuntu)
      case "${VERSION_ID:-}" in
        22.04|24.04) ok "Ubuntu ${VERSION_ID}" ;;
        *) warn "Ubuntu ${VERSION_ID:-unknown} is not one of the tested releases (22.04, 24.04)." ;;
      esac ;;
    debian)
      warn "Debian ${VERSION_ID:-unknown}: usually works, but only Ubuntu is tested." ;;
    *)
      die "Unsupported distribution: ${ID:-unknown}. This installer targets Ubuntu 22.04 or 24.04." ;;
  esac
}

check_arch() {
  local arch; arch="$(uname -m)"
  case "$arch" in
    x86_64|aarch64) ok "architecture ${arch}" ;;
    *) warn "architecture ${arch} is untested; hardware transcoding and the prebuilt
     emulator runtimes may be unavailable." ;;
  esac
}

check_systemd() {
  if [[ -d /run/systemd/system ]]; then
    ok "systemd is available"
  elif (( DRY_RUN )); then
    warn "systemd is not running; a dry run can still check everything else."
  else
    die "systemd is not running.
     CineMediaVault's services, timers and self-healing health check all need it.
     Install on a normal Ubuntu VM rather than a minimal container."
  fi
}

python_ok() {
  local candidate="$1"
  command -v "$candidate" >/dev/null 2>&1 || return 1
  "$candidate" - <<PY >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info[:2] >= (${MIN_PYTHON_MAJOR}, ${MIN_PYTHON_MINOR}) else 1)
PY
}

find_python() {
  local candidate
  for candidate in python3 python3.12 python3.11 python3.10; do
    if python_ok "$candidate"; then
      command -v "$candidate"
      return 0
    fi
  done
  return 1
}

install_bootstrap_packages() {
  local missing=()
  local package
  for package in "${BOOTSTRAP_PACKAGES[@]}"; do
    if ! dpkg-query -W -f='${db:Status-Status}' "$package" 2>/dev/null | grep -q '^installed$'; then
      missing+=("$package")
    fi
  done

  if [[ ${#missing[@]} -eq 0 ]]; then
    ok "prerequisites already installed"
    return 0
  fi

  if (( DRY_RUN )); then
    info "would install: ${missing[*]}"
    return 0
  fi

  step "installing prerequisites: ${missing[*]}"
  export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a
  apt-get update -qq || warn "apt-get update reported a problem; continuing"
  apt-get install -y --no-install-recommends "${missing[@]}" \
    || die "could not install the prerequisites: ${missing[*]}"
  ok "prerequisites installed"
}

# ------------------------------------------------------- setup web server ----

lan_address() {
  # The address this host would use to reach the LAN. Contacts nothing:
  # 192.0.2.1 is TEST-NET-1, routed but never answered.
  local address
  address="$("$PYTHON" - <<'PY' 2>/dev/null || true
import socket, ipaddress
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
try:
    s.connect(("192.0.2.1", 9))
    addr = s.getsockname()[0]
    ip = ipaddress.ip_address(addr)
    print(addr if (ip.is_private and not ip.is_unspecified) else "")
except OSError:
    print("")
finally:
    s.close()
PY
)"
  printf '%s' "${address:-127.0.0.1}"
}

port_is_free() {
  "$PYTHON" - "$1" <<'PY'
import socket, sys
port = int(sys.argv[1])
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    s.bind(("0.0.0.0", port))
    raise SystemExit(0)
except OSError:
    raise SystemExit(1)
finally:
    s.close()
PY
}

open_browser() {
  local url="$1"
  (( NO_BROWSER )) && return 0
  # Only meaningful when someone is sitting at a desktop on this machine.
  [[ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]] || return 0
  local opener
  for opener in xdg-open gnome-open; do
    if command -v "$opener" >/dev/null 2>&1; then
      # Open as the desktop user, not as root.
      local desktop_user="${SUDO_USER:-}"
      if [[ -n "$desktop_user" ]]; then
        sudo -u "$desktop_user" "$opener" "$url" >/dev/null 2>&1 &
      else
        "$opener" "$url" >/dev/null 2>&1 &
      fi
      return 0
    fi
  done
  return 0
}

run_wizard() {
  local host="$SETUP_HOST"
  [[ -n "$host" ]] || host="$(lan_address)"

  if ! port_is_free "$SETUP_PORT"; then
    die "port ${SETUP_PORT} is already in use.
     Choose another with:  sudo ./install.sh --port 8100"
  fi

  # A one-time code, printed here and nowhere else. Reaching the setup page
  # therefore requires access to this console or an SSH session on this machine.
  local token
  token="$("$PYTHON" -c 'import secrets; print(secrets.token_urlsafe(18))')"

  local state_dir="/var/lib/cinemediavault"
  mkdir -p "$state_dir"
  chmod 750 "$state_dir"

  local url="http://${host}:${SETUP_PORT}/"

  printf '\n'
  printf '%s\n' "${C_BOLD}  Open the setup page in a browser${C_RESET}"
  printf '\n'
  printf '%s\n' "      ${C_BOLD}${C_BLUE}${url}${C_RESET}"
  printf '\n'
  printf '%s\n' "  and enter this setup code:"
  printf '\n'
  printf '%s\n' "      ${C_BOLD}${token}${C_RESET}"
  printf '\n'
  printf '%s\n' "${C_DIM}  The page is reachable only from your local network, and this service"
  printf '%s\n' "  stops on its own when the installation finishes."
  printf '%s\n' "  Press Ctrl-C here to stop it sooner.${C_RESET}"
  printf '\n'

  open_browser "$url"

  CMV_SETUP_TOKEN="$token" \
  CMV_PACKAGE_ROOT="$PACKAGE_ROOT" \
  exec "$PYTHON" "$PACKAGE_ROOT/wizard/server.py" \
    --host "$host" \
    --port "$SETUP_PORT" \
    --state "$state_dir/setup-state.json"
}

run_unattended() {
  local args=(--config "$CONFIG_FILE")
  (( DRY_RUN )) && args+=(--dry-run)
  (( ASSUME_YES )) && args+=(--yes)
  (( OFFLINE )) && args+=(--offline)

  step "installing from ${CONFIG_FILE}"
  cd "$PACKAGE_ROOT"
  exec "$PYTHON" -m installer "${args[@]:0:2}" install "${args[@]:2}" \
    ${EXTRA_ARGS+"${EXTRA_ARGS[@]}"}
}

install_cli_link() {
  # Make `cinevaultctl` available before the engine runs, so a failed install
  # can still be inspected, repaired or rolled back from the command line.
  (( DRY_RUN )) && return 0
  local wrapper=/usr/local/sbin/cinevaultctl
  cat > "$wrapper" <<WRAP
#!/usr/bin/env bash
# CineMediaVault control command. Installed by install.sh.
exec ${PYTHON} -m installer "\$@"
WRAP
  chmod 755 "$wrapper"
  # The engine runs from the installed tree once that exists; until then it
  # runs from this package.
  if [[ -d /opt/cinemediavault/installer ]]; then
    sed -i "s|exec ${PYTHON} -m installer|cd /opt/cinemediavault \&\& exec ${PYTHON} -m installer|" "$wrapper"
  else
    sed -i "s|exec ${PYTHON} -m installer|cd ${PACKAGE_ROOT} \&\& exec ${PYTHON} -m installer|" "$wrapper"
  fi
}

# ------------------------------------------------------------------ main ----

main() {
  banner

  if (( ! DRY_RUN )); then
    require_root "$@"
  elif [[ "$(id -u)" -ne 0 ]]; then
    warn "not running as root: a dry run can check most things, but some
     checks (package state, service state) will be incomplete."
  fi

  step "checking this machine"
  check_os
  check_arch
  check_systemd

  PYTHON="$(find_python || true)"
  if [[ -z "${PYTHON:-}" ]]; then
    if (( DRY_RUN )); then
      die "Python ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR}+ is required and was not found."
    fi
    step "installing Python"
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq && apt-get install -y --no-install-recommends python3 \
      || die "could not install Python 3"
    PYTHON="$(find_python || true)"
    [[ -n "${PYTHON:-}" ]] || die "Python ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR}+ is still not available."
  fi
  readonly PYTHON
  ok "Python at ${PYTHON} ($("$PYTHON" -c 'import platform;print(platform.python_version())'))"

  [[ -d "$PACKAGE_ROOT/installer" ]] || die "this package looks incomplete: $PACKAGE_ROOT/installer is missing"
  [[ -d "$PACKAGE_ROOT/payload/app" ]] || die "this package looks incomplete: $PACKAGE_ROOT/payload/app is missing"
  ok "installer package at ${PACKAGE_ROOT}"

  install_bootstrap_packages

  if [[ "$(id -u)" -eq 0 ]]; then
    install_cli_link
    ok "cinevaultctl installed"
  fi

  if [[ -n "$CONFIG_FILE" ]]; then
    [[ -r "$CONFIG_FILE" ]] || die "cannot read configuration file: $CONFIG_FILE"
    run_unattended
  else
    if (( DRY_RUN )); then
      die "--dry-run needs a configuration file to check.
     Create one with:  ./install.sh --help  then  cinevaultctl config example > my.yaml
     Or run without --dry-run to use the setup website."
    fi
    run_wizard
  fi
}

main "$@"
