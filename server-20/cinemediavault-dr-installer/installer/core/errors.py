"""Installer exception types."""

from __future__ import annotations


class InstallerError(Exception):
    """Base class for every installer failure that is not a bug."""

    exit_code = 1


class ConfigError(InstallerError):
    """Configuration is missing, malformed or invalid."""
    exit_code = 2


class PreflightError(InstallerError):
    """A preflight check failed hard enough to stop the installation."""
    exit_code = 3


class StepError(InstallerError):
    """An installation step failed."""
    exit_code = 4

    def __init__(self, message: str, *, step: str = "", recoverable: bool = True):
        super().__init__(message)
        self.step = step
        self.recoverable = recoverable


class CommandError(InstallerError):
    """A subprocess exited non-zero."""
    exit_code = 5

    def __init__(self, argv, returncode: int, stdout: str = "", stderr: str = ""):
        self.argv = list(argv)
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        shown = " ".join(self.argv[:6]) + (" ..." if len(self.argv) > 6 else "")
        detail = (stderr or stdout or "").strip().splitlines()
        tail = detail[-1] if detail else ""
        super().__init__(f"command failed ({returncode}): {shown}" + (f": {tail}" if tail else ""))


class RollbackError(InstallerError):
    """Rollback itself failed; the system may be in a mixed state."""
    exit_code = 6


class AbortedError(InstallerError):
    """The operator cancelled."""
    exit_code = 130
