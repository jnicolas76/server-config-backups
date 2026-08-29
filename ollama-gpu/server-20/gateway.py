#!/usr/bin/env python3
import ipaddress
import html
import json
import os
import re
import socket
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.parse import parse_qs, quote_plus, unquote, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zoneinfo import ZoneInfo

SUBNET = ipaddress.ip_network("192.168.1.0/24")
TOKEN_FILE = "/home/jnicolas/.config/ollama-network-gateway/token"
SSH_KEY = "/home/jnicolas/.ssh/ollama_gateway_ed25519"
MANAGED = {
    "ollama": None,
    "server19": "192.168.1.19",
    "raspberrypi": "192.168.1.123",
    "docker134": "192.168.1.134",
    "transcode232": "192.168.1.232",
}
COMMANDS = {
    "summary": ["hostname", "uptime", "df -h --output=source,size,used,avail,pcent,target -x tmpfs -x devtmpfs", "free -h"],
    "containers": ["docker ps --no-trunc"],
    "failed_services": ["systemctl --failed --no-pager"],
    "temperatures": ["sensors 2>/dev/null || true"],
    "network": ["ip -brief address", "ip route"],
}
LOCAL_TIMEZONE = "America/Denver"
USER_AGENT = "CineVault-LLM-Internet-Tool/1.0"
MAX_PAGE_BYTES = 1_000_000

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def current_time():
    now_utc = datetime.now(timezone.utc)
    now_local = now_utc.astimezone(ZoneInfo(LOCAL_TIMEZONE))
    return {
        "timezone": LOCAL_TIMEZONE,
        "local_iso": now_local.isoformat(),
        "local_date": now_local.strftime("%A, %B %d, %Y"),
        "local_time": now_local.strftime("%I:%M:%S %p %Z"),
        "utc_iso": now_utc.isoformat(),
        "unix_timestamp": int(now_utc.timestamp()),
    }

def validate_public_url(url):
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Only public HTTP and HTTPS URLs are allowed")
    if parsed.username or parsed.password:
        raise ValueError("Credentials in URLs are not allowed")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(parsed.hostname, parsed.port or 443)}
    except socket.gaierror as exc:
        raise ValueError("Host could not be resolved") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise ValueError("Private, local, and reserved network targets are blocked")
    return parsed

def internet_request(url):
    validate_public_url(url)
    opener = build_opener(NoRedirect)
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,text/plain,application/json"})
    try:
        response = opener.open(request, timeout=15)
    except HTTPError as exc:
        if exc.code not in {301, 302, 303, 307, 308}:
            raise
        location = exc.headers.get("Location")
        if not location:
            raise ValueError("Redirect did not include a destination")
        from urllib.parse import urljoin
        redirected = urljoin(url, location)
        validate_public_url(redirected)
        response = opener.open(Request(redirected, headers={"User-Agent": USER_AGENT}), timeout=15)
    with response:
        content_type = response.headers.get("Content-Type", "")
        payload = response.read(MAX_PAGE_BYTES + 1)
        final_url = response.geturl()
    if len(payload) > MAX_PAGE_BYTES:
        payload = payload[:MAX_PAGE_BYTES]
    charset_match = re.search(r"charset=([^; ]+)", content_type, re.I)
    charset = charset_match.group(1).strip('"') if charset_match else "utf-8"
    return final_url, content_type, payload.decode(charset, errors="replace")

def text_from_html(document):
    document = re.sub(r"(?is)<(script|style|svg|noscript).*?>.*?</\\1>", " ", document)
    document = re.sub(r"(?s)<[^>]+>", " ", document)
    return re.sub(r"\\s+", " ", html.unescape(document)).strip()

def web_search(query):
    query = query.strip()[:300]
    if not query:
        raise ValueError("Search query is required")
    url = "https://www.bing.com/search?format=rss&q=" + quote_plus(query)
    _, _, document = internet_request(url)
    root = ET.fromstring(document)
    results = []
    for item in root.findall("./channel/item")[:8]:
        results.append({
            "title": (item.findtext("title") or "").strip(),
            "url": (item.findtext("link") or "").strip(),
            "snippet": text_from_html(item.findtext("description") or "")[:500],
        })
    return {"query": query, "result_count": len(results), "results": results}

def fetch_public_page(url):
    final_url, content_type, document = internet_request(url.strip()[:2000])
    if "html" in content_type:
        text = text_from_html(document)
    else:
        text = re.sub(r"\\s+", " ", document).strip()
    return {"url": final_url, "content_type": content_type, "text": text[:30000], "truncated": len(text) > 30000}

def run(argv, timeout=20):
    result = subprocess.run(argv, text=True, capture_output=True, timeout=timeout, check=False)
    return {"exit_code": result.returncode, "stdout": result.stdout[-30000:], "stderr": result.stderr[-10000:]}

def managed_command(alias, action):
    if alias not in MANAGED or action not in COMMANDS:
        raise ValueError("Unknown managed host or diagnostic action")
    command = " ; ".join(COMMANDS[action])
    host = MANAGED[alias]
    if host is None:
        return run(["bash", "-lc", command])
    return run([
        "ssh", "-i", SSH_KEY, "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
        "-o", "StrictHostKeyChecking=accept-new", "-o", "UpdateHostKeys=no",
        f"jnicolas@{host}", command
    ], timeout=30)

def inventory():
    result = run(["nmap", "-sn", "-oG", "-", str(SUBNET)], timeout=30)
    hosts = []
    for line in result["stdout"].splitlines():
        match = re.match(r"Host: ([0-9.]+) \(([^)]*)\).*Status: Up", line)
        if match:
            hosts.append({"ip": match.group(1), "name": match.group(2) or None})
    return {"count": len(hosts), "hosts": hosts}

def probe(ip_text):
    ip = ipaddress.ip_address(ip_text)
    if ip not in SUBNET:
        raise ValueError("Only 192.168.1.0/24 targets are allowed")
    return run(["nmap", "-sT", "--top-ports", "30", "--host-timeout", "15s", str(ip)], timeout=20)

def schema():
    return {
        "openapi": "3.1.0",
        "info": {"title": "Home Network Read-Only Gateway", "version": "1.0.0"},
        "servers": [{"url": "http://host.docker.internal:8765"}],
        "paths": {
            "/time": {"get": {"operationId": "currentDateTime", "summary": "Get the exact current local and UTC date and time", "responses": {"200": {"description": "Current date and time"}}}},
            "/internet/search": {"get": {"operationId": "searchInternet", "summary": "Search the public internet for current information", "parameters": [{"name": "q", "in": "query", "required": True, "schema": {"type": "string"}}], "responses": {"200": {"description": "Search results"}}}},
            "/internet/page": {"get": {"operationId": "readInternetPage", "summary": "Read text from a public web page; private and local network addresses are blocked", "parameters": [{"name": "url", "in": "query", "required": True, "schema": {"type": "string", "format": "uri"}}], "responses": {"200": {"description": "Extracted page text"}}}},
            "/inventory": {"get": {"operationId": "inventory", "summary": "Discover active devices on the home LAN", "responses": {"200": {"description": "Active devices"}}}},
            "/probe/{ip}": {"get": {"operationId": "probeHost", "summary": "Read-only scan of common TCP ports on one LAN device", "parameters": [{"name": "ip", "in": "path", "required": True, "schema": {"type": "string"}}], "responses": {"200": {"description": "Port scan"}}}},
            "/managed/{alias}/{action}": {"get": {"operationId": "managedDiagnostic", "summary": "Run an allowlisted read-only diagnostic on a managed Linux system", "parameters": [{"name": "alias", "in": "path", "required": True, "schema": {"type": "string", "enum": list(MANAGED)}}, {"name": "action", "in": "path", "required": True, "schema": {"type": "string", "enum": list(COMMANDS)}}], "responses": {"200": {"description": "Diagnostic output"}}}}
        },
        "components": {"securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer"}}},
        "security": [{"bearerAuth": []}],
    }

class Handler(BaseHTTPRequestHandler):
    token = ""

    def reply(self, status, body):
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/health":
            return self.reply(200, {"status": "ok", "mode": "read-only"})
        if path == "/openapi.json":
            return self.reply(200, schema())
        if self.headers.get("Authorization") != f"Bearer {self.token}":
            return self.reply(401, {"error": "Unauthorized"})
        try:
            if path == "/time":
                return self.reply(200, current_time())
            if path == "/internet/search":
                query = parse_qs(urlparse(self.path).query).get("q", [""])[0]
                return self.reply(200, web_search(query))
            if path == "/internet/page":
                url = parse_qs(urlparse(self.path).query).get("url", [""])[0]
                return self.reply(200, fetch_public_page(url))
            if path == "/inventory":
                return self.reply(200, inventory())
            parts = path.strip("/").split("/")
            if len(parts) == 2 and parts[0] == "probe":
                return self.reply(200, probe(parts[1]))
            if len(parts) == 3 and parts[0] == "managed":
                return self.reply(200, managed_command(parts[1], parts[2]))
            return self.reply(404, {"error": "Not found"})
        except (ValueError, subprocess.TimeoutExpired) as exc:
            return self.reply(400, {"error": str(exc)})
        except Exception as exc:
            return self.reply(500, {"error": type(exc).__name__})

    def log_message(self, fmt, *args):
        print(f"{self.client_address[0]} {fmt % args}", flush=True)

def main():
    with open(TOKEN_FILE, encoding="utf-8") as handle:
        Handler.token = handle.read().strip()
    server = ThreadingHTTPServer(("0.0.0.0", 8765), Handler)
    server.serve_forever()

if __name__ == "__main__":
    main()
