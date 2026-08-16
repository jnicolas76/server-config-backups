#!/usr/bin/env python3
"""CineVault server-side cast discovery/controller.

Runs on the CineVault host and exposes LAN device discovery to the browser.
Chromecast/Google Cast devices can be controlled directly. SSDP devices are
listed for visibility but generic playback control is device-specific.
"""

from __future__ import annotations

import json
import socket
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

import pychromecast


HOST = "0.0.0.0"
PORT = 8120
DISCOVERY_TIMEOUT = 5
SSDP_TIMEOUT = 2
SERVER_NAME = "CineVault Cast Controller"


def json_response(handler: BaseHTTPRequestHandler, status: int, payload: Any) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.end_headers()
    handler.wfile.write(data)


def parse_headers(raw: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    for line in raw.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        headers[key.strip().lower()] = value.strip()
    return headers


def discover_ssdp() -> list[dict[str, Any]]:
    message = "\r\n".join(
        [
            "M-SEARCH * HTTP/1.1",
            "HOST: 239.255.255.250:1900",
            'MAN: "ssdp:discover"',
            "MX: 1",
            "ST: ssdp:all",
            "",
            "",
        ]
    ).encode("utf-8")
    devices: dict[str, dict[str, Any]] = {}
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.settimeout(SSDP_TIMEOUT)
    try:
        sock.sendto(message, ("239.255.255.250", 1900))
        end = time.monotonic() + SSDP_TIMEOUT
        while time.monotonic() < end:
            try:
                data, addr = sock.recvfrom(8192)
            except socket.timeout:
                break
            headers = parse_headers(data.decode("utf-8", "replace"))
            usn = headers.get("usn") or f"ssdp:{addr[0]}:{headers.get('st','unknown')}"
            server = headers.get("server", "")
            st = headers.get("st", "")
            location = headers.get("location", "")
            name = "SSDP Device"
            lower = " ".join([server, st, location]).lower()
            if "roku" in lower:
                name = "Roku"
            elif "samsung" in lower:
                name = "Samsung TV"
            elif "dlna" in lower or "mediarenderer" in lower:
                name = "DLNA Renderer"
            else:
                continue
            dedupe_key = f"{name}|{addr[0]}"
            devices[dedupe_key] = {
                "id": f"ssdp:{uuid.uuid5(uuid.NAMESPACE_URL, dedupe_key)}",
                "name": name,
                "type": "ssdp",
                "host": addr[0],
                "port": None,
                "model": server or st or "SSDP",
                "location": location,
                "playable": False,
                "note": "Discovered by SSDP. Playback control is not enabled for this device type yet.",
            }
    finally:
        sock.close()
    return sorted(devices.values(), key=lambda item: (item["name"], item["host"]))


def discover_chromecasts() -> list[dict[str, Any]]:
    chromecasts, browser = pychromecast.get_chromecasts(timeout=DISCOVERY_TIMEOUT)
    try:
        devices = []
        for cast in chromecasts:
            devices.append(
                {
                    "id": str(cast.uuid),
                    "name": cast.name,
                    "type": "chromecast",
                    "host": cast.cast_info.host,
                    "port": cast.cast_info.port,
                    "model": cast.model_name,
                    "manufacturer": cast.cast_info.manufacturer,
                    "playable": True,
                    "note": "Google Cast device",
                }
            )
        return sorted(devices, key=lambda item: item["name"].lower())
    finally:
        pychromecast.discovery.stop_discovery(browser)


def find_chromecast(device_id: str):
    chromecasts, browser = pychromecast.get_chromecasts(timeout=DISCOVERY_TIMEOUT)
    try:
        for cast in chromecasts:
            if str(cast.uuid) == device_id or cast.name == device_id:
                return cast
    finally:
        pychromecast.discovery.stop_discovery(browser)
    return None


def content_type_for(url: str) -> str:
    path = urlparse(url).path.lower()
    if path.endswith(".m3u8"):
        return "application/x-mpegURL"
    if path.endswith(".mp4") or "/stream/" in path or "/media/" in path:
        return "video/mp4"
    if path.endswith(".mkv"):
        return "video/x-matroska"
    return "video/mp4"


class Handler(BaseHTTPRequestHandler):
    server_version = SERVER_NAME

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.address_string()} - {fmt % args}", flush=True)

    def do_OPTIONS(self) -> None:
        json_response(self, 200, {"ok": True})

    def do_GET(self) -> None:
        if self.path in {"/", "/health"}:
            json_response(self, 200, {"ok": True, "service": SERVER_NAME})
            return
        if self.path.startswith("/api/cast/devices"):
            devices = []
            errors = []
            try:
                devices.extend(discover_chromecasts())
            except Exception as exc:
                errors.append(f"chromecast discovery failed: {exc}")
            try:
                devices.extend(discover_ssdp())
            except Exception as exc:
                errors.append(f"ssdp discovery failed: {exc}")
            json_response(self, 200, {"ok": True, "devices": devices, "errors": errors})
            return
        json_response(self, 404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0") or "0")
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except json.JSONDecodeError:
            json_response(self, 400, {"ok": False, "error": "invalid json"})
            return
        if self.path.startswith("/api/cast/play"):
            device_id = str(payload.get("device_id") or "")
            media_url = str(payload.get("media_url") or "")
            title = str(payload.get("title") or "CineVault")
            if not device_id or not media_url:
                json_response(self, 400, {"ok": False, "error": "device_id and media_url are required"})
                return
            cast = find_chromecast(device_id)
            if not cast:
                json_response(self, 404, {"ok": False, "error": "chromecast device not found"})
                return
            try:
                cast.wait(timeout=10)
                controller = cast.media_controller
                controller.play_media(media_url, content_type_for(media_url), title=title)
                controller.block_until_active(timeout=10)
                json_response(self, 200, {"ok": True, "device": cast.name, "media_url": media_url})
            except Exception as exc:
                json_response(self, 500, {"ok": False, "error": str(exc)})
            return
        json_response(self, 404, {"ok": False, "error": "not found"})


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"{SERVER_NAME} listening on http://{HOST}:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
