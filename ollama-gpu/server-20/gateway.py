#!/usr/bin/env python3
import ipaddress
import json
import os
import re
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

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
