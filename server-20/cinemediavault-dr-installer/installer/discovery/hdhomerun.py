"""HDHomeRun discovery, lineup and tuner inspection.

Everything in this module is read-only HTTP against the tuner and, optionally,
SiliconDust's discovery service. No tuner is ever reserved, no channel scan is
started, and no device configuration is written: a scan takes a tuner offline
and would interrupt whatever the household is watching.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from typing import Any

DISCOVERY_URL = "https://ipv4.my.hdhomerun.com/discover"
UDP_DISCOVERY_PORT = 65001
USER_AGENT = "CineMediaVault-Installer/2.0"

DEFAULT_TIMEOUT = 4.0


def _get_json(url: str, timeout: float = DEFAULT_TIMEOUT) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            if response.status != 200:
                return None
            body = response.read(4 * 1024 * 1024)
        return json.loads(body.decode("utf-8", errors="replace"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError):
        return None


def discover(*, runner=None, timeout: float = DEFAULT_TIMEOUT) -> list[dict]:
    """Find HDHomeRun devices on this network.

    Two independent methods are tried and merged, because each fails in
    different environments: the vendor's cloud discovery needs internet access,
    and the UDP broadcast needs a flat layer-2 path to the tuner.
    """
    found: dict[str, dict] = {}

    for entry in _get_json(DISCOVERY_URL, timeout=timeout) or []:
        if not isinstance(entry, dict):
            continue
        url = entry.get("DiscoverURL") or ""
        detail = _get_json(url, timeout=timeout) if url else None
        merged = {**entry, **(detail or {})}
        key = str(merged.get("DeviceID") or merged.get("LocalIP") or len(found))
        found[key] = merged

    for entry in _udp_discover(timeout=min(timeout, 3.0)):
        key = str(entry.get("DeviceID") or entry.get("LocalIP") or len(found))
        if key not in found:
            found[key] = entry

    return list(found.values())


def _udp_discover(timeout: float = 3.0) -> list[dict]:
    """Broadcast the HDHomeRun discovery packet and resolve each responder."""
    # Type 0x0002 (discover request), device type = tuner (0xFFFFFFFF wildcard),
    # device id wildcard. Kept as a literal so no protocol library is needed.
    packet = bytes.fromhex("00020000000c00000001ffffffff00000002ffffffff4e507f35")
    addresses: set[str] = set()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(timeout)
        try:
            sock.sendto(packet, ("255.255.255.255", UDP_DISCOVERY_PORT))
        except OSError:
            return []
        import time
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                _, addr = sock.recvfrom(2048)
                addresses.add(addr[0])
            except socket.timeout:
                break
            except OSError:
                break
    finally:
        sock.close()

    devices: list[dict] = []
    for address in sorted(addresses):
        info = probe_device(address)
        if info:
            devices.append(info)
    return devices


def probe_device(address: str, *, runner=None, timeout: float = DEFAULT_TIMEOUT) -> dict | None:
    """Read ``/discover.json`` from one tuner. Returns None when unreachable."""
    address = (address or "").strip()
    if not address:
        return None
    if "://" in address:
        base = address.rstrip("/")
    else:
        base = f"http://{address}"
    info = _get_json(f"{base}/discover.json", timeout=timeout)
    if not isinstance(info, dict):
        return None
    info.setdefault("LocalIP", address.replace("http://", "").split("/")[0])
    return info


def tuner_count(device: dict | None) -> int:
    if not device:
        return 0
    try:
        return int(device.get("TunerCount") or 0)
    except (TypeError, ValueError):
        return 0


def lineup(address: str, *, timeout: float = 10.0) -> list[dict]:
    """Fetch the channel lineup. Returns [] when the tuner has not scanned yet."""
    device = probe_device(address, timeout=timeout)
    if not device:
        return []
    url = device.get("LineupURL") or f"http://{device.get('LocalIP', address)}/lineup.json"
    data = _get_json(url, timeout=timeout)
    if not isinstance(data, list):
        return []
    channels: list[dict] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        channels.append({
            "number": str(entry.get("GuideNumber") or "").strip(),
            "name": str(entry.get("GuideName") or "").strip(),
            "hd": bool(entry.get("HD")),
            "drm": bool(entry.get("DRM")),
            "favorite": bool(entry.get("Favorite")),
            "url": str(entry.get("URL") or ""),
        })
    channels.sort(key=_channel_sort_key)
    return channels


def _channel_sort_key(channel: dict):
    number = channel.get("number") or ""
    try:
        major, _, minor = number.partition(".")
        return (int(major or 0), int(minor or 0), channel.get("name", ""))
    except ValueError:
        return (9999, 0, channel.get("name", ""))


def status(address: str, *, timeout: float = 5.0) -> list[dict]:
    """Read live tuner status. Used to avoid claiming a tuner already in use."""
    device = probe_device(address, timeout=timeout)
    if not device:
        return []
    base = f"http://{device.get('LocalIP', address)}"
    data = _get_json(f"{base}/status.json", timeout=timeout)
    return data if isinstance(data, list) else []


def summarise(device: dict) -> dict:
    """The subset the wizard shows and the installer records."""
    return {
        "device_id": str(device.get("DeviceID") or ""),
        "model": str(device.get("ModelNumber") or device.get("FriendlyName") or ""),
        "friendly_name": str(device.get("FriendlyName") or ""),
        "address": str(device.get("LocalIP") or ""),
        "tuner_count": tuner_count(device),
        "firmware": str(device.get("FirmwareVersion") or ""),
        "legacy": bool(device.get("Legacy")),
        "device_auth": bool(device.get("DeviceAuth")),
    }
