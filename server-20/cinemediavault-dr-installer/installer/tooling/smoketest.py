"""Clean-VM smoke test.

Answers one question: *is this installation actually working?* Every check
observes real behaviour - a port answering, a database row existing, a timer
scheduled - rather than asserting that a file was written. It is safe to run at
any time on a live system: nothing is modified, no tuner is claimed and no scan
is started.
"""

from __future__ import annotations

import json
import socket
import sqlite3
import ssl
import stat
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

PASS = "pass"
FAIL = "fail"
WARN = "warn"
SKIP = "skip"


@dataclass
class Check:
    name: str
    status: str
    detail: str = ""
    duration: float = 0.0


@dataclass
class SmokeReport:
    checks: list[Check] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)

    def add(self, name: str, status: str, detail: str = "", duration: float = 0.0):
        self.checks.append(Check(name, status, detail, duration))
        return self

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if c.status == FAIL]

    @property
    def warnings(self) -> list[Check]:
        return [c for c in self.checks if c.status == WARN]

    @property
    def ok(self) -> bool:
        return not self.failures

    def to_json(self) -> dict:
        return {
            "ok": self.ok,
            "duration": round(time.time() - self.started_at, 2),
            "total": len(self.checks),
            "passed": sum(1 for c in self.checks if c.status == PASS),
            "failed": len(self.failures),
            "warnings": len(self.warnings),
            "skipped": sum(1 for c in self.checks if c.status == SKIP),
            "checks": [
                {"name": c.name, "status": c.status, "detail": c.detail,
                 "duration": round(c.duration, 3)}
                for c in self.checks
            ],
        }

    def render(self) -> str:
        symbols = {PASS: "PASS", FAIL: "FAIL", WARN: "WARN", SKIP: "SKIP"}
        lines = ["", "CineMediaVault smoke test", "=" * 60]
        for check in self.checks:
            line = f"  [{symbols[check.status]}] {check.name}"
            if check.detail:
                line += f"\n         {check.detail}"
            lines.append(line)
        lines.append("=" * 60)
        summary = self.to_json()
        lines.append(
            f"  {summary['passed']} passed, {summary['failed']} failed, "
            f"{summary['warnings']} warnings, {summary['skipped']} skipped "
            f"in {summary['duration']}s")
        lines.append("  RESULT: " + ("PASS" if self.ok else "FAIL"))
        lines.append("")
        return "\n".join(lines)


def run(ctx, *, deep: bool = False) -> SmokeReport:
    report = SmokeReport()
    _check_services(ctx, report)
    _check_http(ctx, report)
    _check_permissions(ctx, report)
    _check_database(ctx, report)
    _check_timers(ctx, report)
    _check_paths(ctx, report)
    _check_modules(ctx, report)
    _check_epg(ctx, report)
    if deep:
        _check_deep(ctx, report)
    return report


def _timed(fn):
    started = time.monotonic()
    result = fn()
    return result, time.monotonic() - started


def _check_services(ctx, report: SmokeReport) -> None:
    active = ctx.runner.systemd_unit_active("cinemediavault.service")
    report.add("cinemediavault.service is active", PASS if active else FAIL,
               "" if active else "run: systemctl status cinemediavault.service")

    if active:
        result = ctx.runner.probe(
            ["systemctl", "show", "cinemediavault.service",
             "-p", "NRestarts", "--value"])
        try:
            restarts = int(result.stdout.strip() or 0)
        except ValueError:
            restarts = 0
        if restarts > 3:
            report.add("service restart count", WARN,
                       f"the service has restarted {restarts} times; check the logs")
        else:
            report.add("service restart count", PASS, f"{restarts} restarts")


def _check_http(ctx, report: SmokeReport) -> None:
    port = (ctx.get("network.https_port") if ctx.get("network.tls_mode") != "none"
            else ctx.get("network.http_port"))
    scheme = "https" if ctx.get("network.tls_mode") != "none" else "http"

    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE

    for path, name, expect_auth in (
        ("/login", "login page responds", False),
        ("/", "root redirects or responds", False),
        ("/api/db/status", "API requires authentication", True),
        ("/api/genres/movies", "genre discovery API requires authentication", True),
    ):
        url = f"{scheme}://127.0.0.1:{port}{path}"
        started = time.monotonic()
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": "CineMediaVault-SmokeTest"})
            with urllib.request.urlopen(  # noqa: S310 - loopback only
                    request, timeout=15, context=context) as response:
                status = response.status
            elapsed = time.monotonic() - started
            if expect_auth and status == 200:
                report.add(name, WARN,
                           f"{url} returned 200 without a session; confirm the API "
                           f"is not open", elapsed)
            elif 200 <= status < 400:
                report.add(name, PASS, f"HTTP {status}", elapsed)
            else:
                report.add(name, FAIL, f"HTTP {status} from {url}", elapsed)
        except urllib.error.HTTPError as exc:
            elapsed = time.monotonic() - started
            if expect_auth and exc.code in (401, 403):
                report.add(name, PASS, f"HTTP {exc.code} (authentication required)",
                           elapsed)
            elif exc.code in (301, 302, 303, 307, 308):
                report.add(name, PASS, f"HTTP {exc.code}", elapsed)
            else:
                report.add(name, FAIL, f"HTTP {exc.code} from {url}", elapsed)
        except (urllib.error.URLError, socket.timeout, OSError) as exc:
            report.add(name, FAIL, f"{url}: {exc}", time.monotonic() - started)

    if scheme == "https":
        cert_ok = ctx.runner.probe([
            "openssl", "s_client", "-connect", f"127.0.0.1:{port}",
            "-servername", ctx.get("network.hostname") or "localhost",
        ]).returncode == 0
        report.add("TLS handshake succeeds", PASS if cert_ok else WARN,
                   "" if cert_ok else "the port answered but the TLS handshake did not "
                                      "complete cleanly")


def _check_permissions(ctx, report: SmokeReport) -> None:
    layout = ctx.layout
    sensitive = [
        (layout.secrets_file, 0o640, "secrets file"),
        (layout.env_file, 0o640, "service environment"),
        (layout.tls_dir / "cinemediavault.key", 0o640, "TLS private key"),
    ]
    for path, expected, label in sensitive:
        if not path.exists():
            report.add(f"{label} permissions", SKIP, f"{path} not present")
            continue
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & 0o007:
            report.add(f"{label} permissions", FAIL,
                       f"{path} is mode {mode:04o}: readable by any account")
        elif mode != expected:
            report.add(f"{label} permissions", WARN,
                       f"{path} is mode {mode:04o}, expected {expected:04o}")
        else:
            report.add(f"{label} permissions", PASS, f"{mode:04o}")

    if layout.admin_bootstrap_file.exists():
        report.add("administrator bootstrap file removed", WARN,
                   f"{layout.admin_bootstrap_file} still exists. It holds the "
                   f"password hash and should be removed once the account is "
                   f"created.")
    else:
        report.add("administrator bootstrap file removed", PASS)


def _check_database(ctx, report: SmokeReport) -> None:
    db = ctx.layout.db_file
    if not db.exists():
        report.add("database exists", FAIL, f"{db} is missing")
        return
    report.add("database exists", PASS, str(db))
    try:
        connection = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=15)
    except sqlite3.Error as exc:
        report.add("database opens", FAIL, str(exc))
        return
    try:
        started = time.monotonic()
        check = connection.execute("PRAGMA quick_check").fetchone()[0]
        elapsed = time.monotonic() - started
        report.add("database integrity", PASS if str(check).lower() == "ok" else FAIL,
                   str(check), elapsed)

        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        required = {"users", "user_sessions"}
        missing = required - tables
        report.add("core schema present", PASS if not missing else FAIL,
                   "" if not missing else f"missing tables: {', '.join(sorted(missing))}")

        admins = connection.execute(
            "SELECT COUNT(*) FROM users WHERE is_super_admin=1 AND active=1"
        ).fetchone()[0]
        report.add("an active super-administrator exists",
                   PASS if admins >= 1 else FAIL, f"{admins} found")

        legacy = connection.execute(
            "SELECT COUNT(*) FROM users WHERE password_hash NOT LIKE 'pbkdf2_sha256$%'"
        ).fetchone()[0]
        report.add("all passwords use PBKDF2", PASS if legacy == 0 else FAIL,
                   "" if legacy == 0 else f"{legacy} account(s) with a foreign hash")

        if ctx.get("dvr.enabled"):
            dvr_tables = {"dvr_settings", "dvr_recordings", "dvr_series_rules"}
            missing_dvr = dvr_tables - tables
            report.add("DVR schema present",
                       PASS if not missing_dvr else WARN,
                       "" if not missing_dvr
                       else f"missing: {', '.join(sorted(missing_dvr))}. The DVR "
                            f"creates them on first use.")

        vchannel_tables = {"vchannel_defs", "vchannel_schedule", "vchannel_build_state"}
        missing_vchannel = vchannel_tables - tables
        report.add("virtual-channel schema present",
                   PASS if not missing_vchannel else WARN,
                   "" if not missing_vchannel
                   else f"missing: {', '.join(sorted(missing_vchannel))}. The "
                        f"application creates them on first start.")
    finally:
        connection.close()


def _check_timers(ctx, report: SmokeReport) -> None:
    expected = ["cinemediavault-refresh.timer", "cinemediavault-backup.timer"]
    if ctx.get("ops.health_check_enabled"):
        expected.append("cinemediavault-health.timer")
    if ctx.get("promo_channel.enabled"):
        expected.append("cinemediavault-promo.timer")
    for timer in expected:
        active = ctx.runner.systemd_unit_active(timer)
        report.add(f"{timer} scheduled", PASS if active else WARN,
                   "" if active else f"run: systemctl enable --now {timer}")


def _check_paths(ctx, report: SmokeReport) -> None:
    for name, path in ctx.media_roots().items():
        target = Path(path)
        if not target.is_dir():
            report.add(f"{name} library reachable", FAIL, f"{path} does not exist")
            continue
        try:
            empty = not any(target.iterdir())
        except OSError as exc:
            report.add(f"{name} library reachable", FAIL, f"{path}: {exc}")
            continue
        if empty:
            report.add(f"{name} library reachable", WARN,
                       f"{path} is empty. If this should hold media, the share is "
                       f"probably not mounted.")
        else:
            report.add(f"{name} library reachable", PASS, path)

    if ctx.get("dvr.enabled"):
        from ..core.fsops import free_bytes, human_bytes
        recordings = ctx.get("media.recordings_root")
        if recordings and Path(recordings).is_dir():
            free = free_bytes(recordings)
            floor = int(ctx.get("dvr.min_free_gb") or 0) * (1024 ** 3)
            report.add("DVR destination has space",
                       PASS if free > floor else FAIL,
                       f"{human_bytes(free)} free (floor "
                       f"{ctx.get('dvr.min_free_gb')} GB)")


def _check_modules(ctx, report: SmokeReport) -> None:
    units: list[tuple[str, str, int]] = []
    if ctx.get("modules.comics"):
        units.append(("cinemediavault-module@comics.service", "Comics",
                      int(ctx.get("modules.comics_port"))))
    if ctx.get("modules.bookvault"):
        units.append(("cinemediavault-bookvault.service", "Book Vault",
                      int(ctx.get("modules.bookvault_port"))))
    base = int(ctx.get("modules.game_port_base") or 8090)
    for index, game in enumerate(ctx.selected_games()):
        units.append((f"cinemediavault-module@{game}.service", game.upper(),
                      base + index))

    for unit, label, port in units:
        active = ctx.runner.systemd_unit_active(unit)
        if not active:
            report.add(f"{label} module running", WARN,
                       f"{unit} is not active")
            continue
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=5):
                report.add(f"{label} module running", PASS, f"port {port}")
        except OSError as exc:
            report.add(f"{label} module running", WARN,
                       f"unit is active but port {port} did not accept: {exc}")


def _check_epg(ctx, report: SmokeReport) -> None:
    if ctx.get("epg.mode") != "extended":
        report.add("extended EPG collector", SKIP, "not enabled")
        return
    if not ctx.runner.has("docker"):
        report.add("extended EPG collector", FAIL, "docker is not available")
        return
    result = ctx.runner.probe(
        ["docker", "inspect", "-f", "{{.State.Status}}", "cinemediavault-epg"])
    status = result.stdout.strip()
    if status != "running":
        report.add("extended EPG collector", WARN,
                   f"container status is '{status or 'absent'}'. The tuner guide "
                   f"still works.")
        return
    report.add("extended EPG collector", PASS, "container running")

    address = ctx.get("epg.collector_bind_address")
    port = int(ctx.get("epg.collector_port"))
    try:
        with socket.create_connection((address, port), timeout=5):
            pass
        report.add("EPG feed reachable", PASS, f"{address}:{port}")
    except OSError as exc:
        report.add("EPG feed reachable", WARN, f"{address}:{port}: {exc}")

    # The collector must not be listening on every interface.
    listeners = ctx.runner.probe(["ss", "-lntH", f"sport = :{port}"]).stdout
    if "0.0.0.0:" in listeners or "*:" in listeners:
        report.add("EPG collector is not public", FAIL,
                   f"port {port} is bound to all interfaces. It must be bound to "
                   f"a private address only.")
    else:
        report.add("EPG collector is not public", PASS)


def _check_deep(ctx, report: SmokeReport) -> None:
    """Slower checks: ffmpeg capability and a real Live TV probe."""
    ffmpeg = ctx.runner.which("ffmpeg")
    if not ffmpeg:
        report.add("ffmpeg available", FAIL, "ffmpeg is not installed")
    else:
        result = ctx.runner.probe([ffmpeg, "-hide_banner", "-encoders"])
        has_h264 = "libx264" in result.stdout
        report.add("ffmpeg available", PASS if has_h264 else WARN,
                   "libx264 present" if has_h264
                   else "libx264 is missing; HLS transcoding will fail")

    if ctx.get("livetv.enabled"):
        from ..discovery import hdhomerun
        address = ctx.get("livetv.device_address")
        device = hdhomerun.probe_device(address) if address else None
        if device:
            channels = hdhomerun.lineup(address)
            report.add("tuner reachable", PASS,
                       f"{hdhomerun.tuner_count(device)} tuner(s), "
                       f"{len(channels)} channel(s)")
            if not channels:
                report.add("tuner lineup populated", WARN,
                           "the tuner reports no channels; run a channel scan from "
                           "its own web interface")
        else:
            report.add("tuner reachable", FAIL,
                       f"no HDHomeRun answered at {address or '<unset>'}")
    else:
        report.add("tuner reachable", SKIP, "Live TV is not enabled")
