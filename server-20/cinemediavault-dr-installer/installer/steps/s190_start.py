"""Start the services and wait for the application to answer."""

from __future__ import annotations

import socket
import ssl
import time
import urllib.error
import urllib.request

from ..core.errors import StepError
from .base import Step, StepResult

START_TIMEOUT = 120.0
POLL_INTERVAL = 2.0


class StartServices(Step):
    id = "start"
    title = "Start CineMediaVault"
    description = "Start the service and wait for it to answer a health request."
    requires = ("timers", "admin")

    def fingerprint_inputs(self, ctx):
        # Always re-run: starting an already-running service is a no-op, and a
        # repair run must be able to bring a stopped service back.
        return [time.time()]

    def preview(self, ctx) -> str:
        return f"Start cinemediavault.service and confirm {ctx.primary_url()} answers."

    def run(self, ctx) -> StepResult:
        if ctx.dry_run:
            return StepResult(summary="would start cinemediavault.service")

        ctx.logger.info("starting cinemediavault.service")
        result = ctx.runner.systemctl("restart", "cinemediavault.service",
                                      check=False, timeout=180)
        if result.returncode != 0:
            raise StepError(
                "cinemediavault.service failed to start. Diagnose with:\n"
                "  systemctl status cinemediavault.service\n"
                "  journalctl -u cinemediavault.service -n 50\n"
                + (result.stderr or "").strip()[-500:],
                step=self.id)

        port = (ctx.get("network.https_port") if ctx.get("network.tls_mode") != "none"
                else ctx.get("network.http_port"))
        scheme = "https" if ctx.get("network.tls_mode") != "none" else "http"
        url = f"{scheme}://127.0.0.1:{port}/login"

        ready, elapsed, detail = self._wait_for(url)
        if not ready:
            log_hint = f"{ctx.layout.log_root}/cinemediavault.err"
            raise StepError(
                f"the service started but did not answer {url} within "
                f"{START_TIMEOUT:.0f}s ({detail}). Check {log_hint} and "
                f"`journalctl -u cinemediavault.service`.",
                step=self.id)

        ctx.service_urls["CineMediaVault"] = ctx.primary_url()

        started = self._start_modules(ctx)
        return StepResult(
            changed=True,
            summary=f"service answered in {elapsed:.1f}s; "
                    f"{len(started)} module service(s) started",
            data={"url": ctx.primary_url(), "modules": started},
        )

    def _wait_for(self, url: str) -> tuple[bool, float, str]:
        context = ssl.create_default_context()
        # The first-run certificate is self-signed by design; this probe only
        # asks "is the application answering", not "is the chain trusted".
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

        started = time.monotonic()
        detail = "no response"
        while time.monotonic() - started < START_TIMEOUT:
            try:
                request = urllib.request.Request(
                    url, headers={"User-Agent": "CineMediaVault-Installer"})
                with urllib.request.urlopen(  # noqa: S310 - fixed loopback URL
                        request, timeout=5, context=context) as response:
                    if 200 <= response.status < 400:
                        return True, time.monotonic() - started, ""
                    detail = f"HTTP {response.status}"
            except urllib.error.HTTPError as exc:
                # A login page that returns 401/403 is still a live application.
                if exc.code in (401, 403):
                    return True, time.monotonic() - started, ""
                detail = f"HTTP {exc.code}"
            except (urllib.error.URLError, ssl.SSLError, socket.timeout, OSError) as exc:
                detail = str(exc)
            time.sleep(POLL_INTERVAL)
        return False, time.monotonic() - started, detail

    def _start_modules(self, ctx) -> list[str]:
        units: list[str] = []
        if ctx.get("modules.comics"):
            units.append("cinemediavault-module@comics.service")
        for game in ctx.selected_games():
            units.append(f"cinemediavault-module@{game}.service")
        if ctx.get("modules.bookvault"):
            units.append("cinemediavault-bookvault.service")
        if ctx.get("subtitles.whisper_enabled") and \
                ctx.get("subtitles.whisper_start_enabled"):
            units.append("cinemediavault-subtitles.service")

        started: list[str] = []
        for unit in units:
            result = ctx.runner.systemctl("restart", unit, check=False, timeout=90)
            if result.returncode == 0:
                started.append(unit)
            else:
                # A module failing to start must not fail the installation: the
                # main application is up and the operator can fix one module.
                ctx.logger.warning(
                    f"{unit} did not start. Check `systemctl status {unit}`.")
        return started


def steps():
    return [StartServices()]
