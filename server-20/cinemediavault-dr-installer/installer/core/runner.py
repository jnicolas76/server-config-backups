"""Subprocess execution.

Every external command is described as an **argument list**. There is no code
path in this installer that builds a shell string from configuration, so an
operator-supplied path containing a space, a quote or a semicolon is data, never
syntax. ``shell=True`` is not used anywhere.
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from typing import Mapping, Sequence

from .errors import CommandError
from .logging import InstallLogger, get_logger
from .redact import REDACTOR


@dataclass
class Result:
    argv: list[str]
    returncode: int
    stdout: str
    stderr: str
    duration: float

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def lines(self) -> list[str]:
        return [line for line in self.stdout.splitlines() if line.strip()]


class Runner:
    """Runs commands, honouring dry-run and redacting everything it logs."""

    def __init__(self, *, dry_run: bool = False, logger: InstallLogger | None = None,
                 default_timeout: float = 300.0):
        self.dry_run = dry_run
        self.logger = logger or get_logger()
        self.default_timeout = default_timeout

    def run(
        self,
        argv: Sequence[str],
        *,
        check: bool = True,
        timeout: float | None = None,
        env: Mapping[str, str] | None = None,
        cwd: str | os.PathLike | None = None,
        input_text: str | None = None,
        user: str | None = None,
        allow_in_dry_run: bool = False,
        quiet: bool = False,
    ) -> Result:
        """Run *argv*. Read-only probes pass ``allow_in_dry_run=True``."""
        argv = [str(a) for a in argv]
        if not argv:
            raise ValueError("empty command")
        shown = " ".join(REDACTOR.redact_argv(argv))

        if self.dry_run and not allow_in_dry_run:
            self.logger.info(f"[dry-run] would run: {shown}")
            return Result(argv, 0, "", "", 0.0)

        if user:
            argv = ["setpriv", "--reuid", user, "--regid", user, "--init-groups", "--"] + argv
            shown = " ".join(REDACTOR.redact_argv(argv))

        if not quiet:
            self.logger.debug(f"run: {shown}")

        full_env = None
        if env is not None:
            full_env = dict(os.environ)
            full_env.update({k: str(v) for k, v in env.items()})

        started = time.monotonic()
        try:
            proc = subprocess.run(          # noqa: S603 - argv list, never a shell
                argv,
                capture_output=True,
                text=True,
                timeout=timeout if timeout is not None else self.default_timeout,
                env=full_env,
                cwd=str(cwd) if cwd else None,
                input=input_text,
                check=False,
            )
        except FileNotFoundError as exc:
            raise CommandError(argv, 127, "", f"command not found: {argv[0]}") from exc
        except subprocess.TimeoutExpired as exc:
            duration = time.monotonic() - started
            raise CommandError(argv, 124, exc.stdout or "",
                               f"timed out after {duration:.0f}s") from exc

        duration = time.monotonic() - started
        result = Result(argv, proc.returncode, proc.stdout or "", proc.stderr or "", duration)

        if not result.ok:
            tail = REDACTOR.redact((result.stderr or result.stdout or "").strip())
            if tail:
                tail = "\n".join(tail.splitlines()[-8:])
            if check:
                self.logger.error(f"command failed ({result.returncode}): {shown}")
                if tail:
                    self.logger.error(tail)
                raise CommandError(argv, result.returncode, result.stdout, result.stderr)
            if not quiet:
                self.logger.debug(f"command returned {result.returncode}: {shown}")
        return result

    # -- convenience --------------------------------------------------------
    def probe(self, argv: Sequence[str], *, timeout: float = 15.0) -> Result:
        """Read-only probe: never fails the run, always allowed in dry-run."""
        return self.run(argv, check=False, timeout=timeout,
                        allow_in_dry_run=True, quiet=True)

    def which(self, name: str) -> str | None:
        import shutil as _shutil
        return _shutil.which(name)

    def has(self, name: str) -> bool:
        return self.which(name) is not None

    # -- systemd ------------------------------------------------------------
    def systemctl(self, *args: str, check: bool = True, timeout: float = 120.0) -> Result:
        return self.run(["systemctl", *args], check=check, timeout=timeout)

    def systemd_unit_active(self, unit: str) -> bool:
        return self.probe(["systemctl", "is-active", "--quiet", unit]).returncode == 0

    def systemd_unit_enabled(self, unit: str) -> bool:
        return self.probe(["systemctl", "is-enabled", "--quiet", unit]).returncode == 0

    def systemd_unit_exists(self, unit: str) -> bool:
        result = self.probe(["systemctl", "list-unit-files", unit])
        return unit in result.stdout

    def daemon_reload(self) -> None:
        self.systemctl("daemon-reload")

    def enable_now(self, unit: str) -> None:
        self.systemctl("enable", "--now", unit)

    def restart(self, unit: str) -> None:
        self.systemctl("restart", unit)

    # -- apt ----------------------------------------------------------------
    def apt_installed(self, package: str) -> bool:
        result = self.probe(["dpkg-query", "-W", "-f=${db:Status-Status}", package])
        return result.returncode == 0 and result.stdout.strip() == "installed"

    def apt_update(self, *, max_age_seconds: float = 3600.0) -> None:
        """Refresh the package index, but not more often than necessary."""
        stamp = "/var/lib/apt/periodic/update-success-stamp"
        lists = "/var/lib/apt/lists"
        newest = 0.0
        for candidate in (stamp, lists):
            try:
                newest = max(newest, os.path.getmtime(candidate))
            except OSError:
                pass
        if newest and (time.time() - newest) < max_age_seconds:
            self.logger.debug("apt index is recent; skipping update")
            return
        self.run(["apt-get", "update"], timeout=600.0,
                 env={"DEBIAN_FRONTEND": "noninteractive"})

    def apt_install(self, packages: Sequence[str], *, timeout: float = 1800.0) -> list[str]:
        """Install only the packages that are actually missing."""
        missing = [p for p in packages if not self.apt_installed(p)]
        if not missing:
            self.logger.debug(f"already installed: {', '.join(packages)}")
            return []
        self.apt_update()
        self.run(
            ["apt-get", "install", "-y", "--no-install-recommends", *missing],
            timeout=timeout,
            env={"DEBIAN_FRONTEND": "noninteractive", "NEEDRESTART_MODE": "a"},
        )
        return missing
