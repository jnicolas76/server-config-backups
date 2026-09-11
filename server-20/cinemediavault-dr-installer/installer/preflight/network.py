"""Port availability, DNS, internet reachability and listen-address checks."""

from __future__ import annotations

import socket
from typing import Iterable

from ..config.schema import is_private_address
from . import CheckResult, fail, ok, warn

#: Hosts the installer needs to reach, and what needs them.
INTERNET_TARGETS = {
    "api.themoviedb.org": "TMDb metadata and posters",
    "image.tmdb.org": "TMDb artwork downloads",
    "github.com": "iptv-org/epg collector source",
    "registry-1.docker.io": "Docker base images for the EPG collector",
    "archive.ubuntu.com": "Ubuntu packages",
}


def port_in_use(port: int, address: str = "0.0.0.0") -> tuple[bool, str]:
    """Return (in_use, owner_hint). Never binds long enough to disturb anything."""
    families = [(socket.AF_INET, address)]
    if address in ("::", "::1"):
        families = [(socket.AF_INET6, address)]
    for family, addr in families:
        sock = socket.socket(family, socket.SOCK_STREAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((addr, port))
            return False, ""
        except OSError as exc:
            return True, str(exc)
        finally:
            sock.close()
    return False, ""


def _listener_owner(ctx, port: int) -> str:
    """Best-effort description of what already holds *port*."""
    result = ctx.runner.probe(["ss", "-lntpH", f"sport = :{port}"])
    if result.returncode == 0 and result.stdout.strip():
        line = result.stdout.strip().splitlines()[0]
        if "users:" in line:
            return line.split("users:", 1)[1].strip()
        return line.strip()
    return ""


def required_ports(ctx) -> list[tuple[int, str]]:
    ports: list[tuple[int, str]] = []
    if ctx.get("network.enable_http"):
        ports.append((int(ctx.get("network.http_port")), "CineMediaVault HTTP"))
    if ctx.get("network.tls_mode") != "none":
        ports.append((int(ctx.get("network.https_port")), "CineMediaVault HTTPS"))
    if ctx.get("modules.bookvault"):
        ports.append((int(ctx.get("modules.bookvault_port")), "Book Vault"))
    if ctx.get("modules.comics"):
        ports.append((int(ctx.get("modules.comics_port")), "Comics library"))
    if ctx.get("epg.mode") == "extended":
        ports.append((int(ctx.get("epg.collector_port")), "EPG collector"))
    base = int(ctx.get("modules.game_port_base") or 8090)
    for offset, game in enumerate(ctx.get("modules.games") or []):
        ports.append((base + offset, f"{game.upper()} module"))
    return ports


def checks(ctx) -> list[CheckResult]:
    results: list[CheckResult] = []

    # -- listen address exists on this host ---------------------------------
    listen = (ctx.get("network.listen_address") or "0.0.0.0").strip()
    if listen in ("0.0.0.0", "::"):
        results.append(ok("net.listen", "Listen address",
                          f"{listen} (all interfaces)"))
    else:
        local = _local_addresses(ctx)
        if listen in local:
            results.append(ok("net.listen", "Listen address", listen))
        else:
            results.append(fail(
                "net.listen", "Listen address",
                f"{listen} is not configured on this host. "
                f"Available: {', '.join(sorted(local)) or 'none detected'}.",
                "Use 0.0.0.0, or one of the addresses listed above."))

    # -- ports ---------------------------------------------------------------
    for port, purpose in required_ports(ctx):
        used, _ = port_in_use(port, "0.0.0.0")
        if not used:
            results.append(ok("net.port", f"Port {port} ({purpose})", "available",
                              port=port, purpose=purpose))
            continue
        owner = _listener_owner(ctx, port)
        # An existing CineMediaVault holding its own port is expected on a
        # repair/upgrade run, not a conflict.
        if "cinemediavault" in owner.lower() or "cinevault" in owner.lower():
            results.append(warn(
                "net.port", f"Port {port} ({purpose})",
                f"already held by an existing CineMediaVault service ({owner}).",
                "The installer will restart that service rather than fail.",
                port=port))
        else:
            results.append(fail(
                "net.port", f"Port {port} ({purpose})",
                f"already in use{(' by ' + owner) if owner else ''}.",
                f"Stop the other service or choose a different port for {purpose}.",
                port=port))

    # -- DNS ------------------------------------------------------------------
    try:
        socket.setdefaulttimeout(5)
        socket.getaddrinfo("api.themoviedb.org", 443)
        results.append(ok("net.dns", "DNS resolution", "working"))
        dns_ok = True
    except OSError as exc:
        dns_ok = False
        results.append(warn(
            "net.dns", "DNS resolution", f"lookup failed: {exc}",
            "Metadata, posters and optional downloads need working DNS. A local "
            "library still installs and plays without it."))
    finally:
        socket.setdefaulttimeout(None)

    # -- internet -------------------------------------------------------------
    if ctx.offline:
        results.append(warn(
            "net.internet", "Internet access",
            "Offline mode requested; no outbound checks were made.",
            "Components that download at install time will be skipped."))
    elif dns_ok:
        unreachable: list[str] = []
        for host, purpose in INTERNET_TARGETS.items():
            if not _reachable(host, 443):
                unreachable.append(f"{host} ({purpose})")
        if not unreachable:
            results.append(ok("net.internet", "Internet access",
                              "all required endpoints reachable"))
        elif len(unreachable) == len(INTERNET_TARGETS):
            results.append(warn(
                "net.internet", "Internet access", "no outbound HTTPS reachable.",
                "The core install works offline, but metadata fetching, the EPG "
                "collector and optional downloads will not."))
        else:
            results.append(warn(
                "net.internet", "Internet access",
                "unreachable: " + "; ".join(unreachable),
                "The components that need those endpoints will be limited."))
    # DNS failure already produced a warning; do not double-report.

    # -- hostname resolves ------------------------------------------------------
    hostname = (ctx.get("network.hostname") or "").strip()
    if hostname and hostname not in ("localhost",):
        try:
            socket.setdefaulttimeout(3)
            socket.getaddrinfo(hostname, None)
            results.append(ok("net.hostname", "Hostname resolution",
                              f"{hostname} resolves"))
        except OSError:
            results.append(warn(
                "net.hostname", "Hostname resolution",
                f"{hostname} does not resolve from this machine.",
                "Add it to your router's DNS or to /etc/hosts on each client, "
                "otherwise browsers must use the IP address and the TLS "
                "certificate name will not match."))
        finally:
            socket.setdefaulttimeout(None)

    # -- trusted networks ---------------------------------------------------------
    for cidr in ctx.get("network.trusted_networks") or []:
        from ..config.schema import is_private_cidr
        if not is_private_cidr(str(cidr)):
            results.append(fail(
                "net.trusted", "Trusted networks",
                f"{cidr} is not a private range.",
                "The setup wizard and EPG collector must never be reachable from "
                "the public internet."))

    # -- HDHomeRun ------------------------------------------------------------------
    if ctx.get("livetv.enabled"):
        results.extend(_hdhomerun_checks(ctx))

    return results


def _hdhomerun_checks(ctx) -> list[CheckResult]:
    from ..discovery import hdhomerun
    results: list[CheckResult] = []
    mode = ctx.get("livetv.discovery")
    if mode == "none":
        results.append(warn("net.hdhr", "HDHomeRun", "discovery disabled",
                            "Live TV will have no tuner until one is configured."))
        return results

    if mode == "manual":
        address = (ctx.get("livetv.device_address") or "").strip()
        device = hdhomerun.probe_device(address, runner=ctx.runner)
        if device:
            results.append(ok("net.hdhr", "HDHomeRun",
                              f"{device.get('FriendlyName', 'tuner')} at {address} "
                              f"({device.get('TunerCount', '?')} tuners)",
                              device=device))
        else:
            results.append(fail(
                "net.hdhr", "HDHomeRun",
                f"No HDHomeRun responded at {address}.",
                "Check the address, that the tuner is powered on, and that this "
                "host is on the same network segment."))
        return results

    devices = hdhomerun.discover(runner=ctx.runner)
    if devices:
        names = ", ".join(
            f"{d.get('DeviceID', '?')}@{d.get('LocalIP', '?')}" for d in devices)
        results.append(ok("net.hdhr", "HDHomeRun",
                          f"{len(devices)} device(s): {names}", devices=devices))
    else:
        results.append(warn(
            "net.hdhr", "HDHomeRun", "no tuner found by automatic discovery.",
            "Discovery uses the HDHomeRun cloud service and a UDP broadcast; both "
            "can be blocked. Enter the tuner's IP address manually instead."))
    return results


def _reachable(host: str, port: int, timeout: float = 4.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _local_addresses(ctx) -> set[str]:
    addresses: set[str] = {"127.0.0.1", "::1"}
    result = ctx.runner.probe(["ip", "-o", "addr", "show"])
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[2] in ("inet", "inet6"):
            addresses.add(parts[3].split("/")[0])
    return addresses


def primary_lan_address() -> str:
    """The address this host would use to reach the LAN. Never contacts anything."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.0.2.1", 9))   # TEST-NET-1: routed, never answered
        addr = sock.getsockname()[0]
        return addr if is_private_address(addr) else "127.0.0.1"
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()
