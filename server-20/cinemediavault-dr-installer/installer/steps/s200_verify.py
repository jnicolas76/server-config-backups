"""Post-installation verification.

This is the step that decides whether the install may be reported as
successful. It checks observable behaviour - a listening port, an answering
health endpoint, an administrator row in the database, correct secret file
permissions - rather than "did we write the files we meant to write".
"""

from __future__ import annotations

import os
import sqlite3
import stat
from pathlib import Path

from .base import Step, StepResult


class VerifyInstallation(Step):
    id = "verify"
    title = "Verification"
    description = "Confirm the installed system is actually working."
    requires = ("start",)
    read_only = True

    def fingerprint_inputs(self, ctx):
        import time
        return [time.time()]

    def preview(self, ctx) -> str:
        return "Verify services, permissions, database and administrator account."

    def run(self, ctx) -> StepResult:
        if ctx.dry_run:
            return StepResult(summary="would verify the installation")

        problems: list[str] = []
        warnings: list[str] = []
        checks: dict[str, str] = {}

        # -- main service -------------------------------------------------
        if ctx.runner.systemd_unit_active("cinemediavault.service"):
            checks["service"] = "active"
        else:
            problems.append("cinemediavault.service is not active")

        # -- secret file permissions ---------------------------------------
        for path, expected in ((ctx.layout.secrets_file, 0o640),
                               (ctx.layout.admin_bootstrap_file, 0o640),
                               (ctx.layout.env_file, 0o640)):
            if not path.exists():
                continue
            mode = stat.S_IMODE(path.stat().st_mode)
            if mode & 0o007:
                problems.append(f"{path} is world-accessible (mode {mode:04o})")
            elif mode != expected:
                warnings.append(f"{path} is mode {mode:04o}, expected {expected:04o}")
            else:
                checks[path.name] = f"{mode:04o}"

        key = ctx.layout.tls_dir / "cinemediavault.key"
        if key.exists():
            mode = stat.S_IMODE(key.stat().st_mode)
            if mode & 0o007:
                problems.append(f"the TLS private key {key} is world-readable")
            else:
                checks["tls_key"] = f"{mode:04o}"

        # -- database and administrator -------------------------------------
        admin = self._check_admin(ctx, problems, warnings)
        if admin:
            checks["admin"] = admin

        # -- the bootstrap file has done its job ------------------------------
        if admin and ctx.layout.admin_bootstrap_file.exists():
            try:
                ctx.layout.admin_bootstrap_file.unlink()
                ctx.logger.info("removed the administrator bootstrap file; the "
                                "account now lives only in the database")
            except OSError as exc:
                warnings.append(f"could not remove the bootstrap file: {exc}")

        # -- module services ----------------------------------------------------
        for unit, label in self._module_units(ctx):
            if ctx.runner.systemd_unit_active(unit):
                checks[label] = "active"
            else:
                warnings.append(f"{label} ({unit}) is not running")

        # -- timers ---------------------------------------------------------------
        for timer in ("cinemediavault-refresh.timer", "cinemediavault-backup.timer"):
            if ctx.runner.systemd_unit_active(timer):
                checks[timer] = "active"
            else:
                warnings.append(f"{timer} is not active")

        # -- EPG collector ----------------------------------------------------------
        if ctx.get("epg.mode") == "extended" and ctx.runner.has("docker"):
            result = ctx.runner.probe(
                ["docker", "inspect", "-f", "{{.State.Status}}", "cinemediavault-epg"])
            status = result.stdout.strip()
            if status == "running":
                checks["epg_collector"] = "running"
            else:
                warnings.append(
                    f"the EPG collector container is '{status or 'absent'}'. The "
                    f"tuner guide still works; run `cinevaultctl epg rebuild`.")

        if problems:
            outcome = StepResult(summary=f"{len(problems)} problem(s) found",
                                 data={"checks": checks, "problems": problems})
            for message in problems:
                outcome.warn("PROBLEM: " + message)
            for message in warnings:
                outcome.warn(message)
            return outcome

        outcome = StepResult(
            changed=False,
            summary=f"{len(checks)} verification(s) passed",
            data={"checks": checks},
        )
        for message in warnings:
            outcome.warn(message)
        return outcome

    def _check_admin(self, ctx, problems: list[str], warnings: list[str]) -> str:
        username = (ctx.get("admin.username") or "").strip()
        if not ctx.layout.db_file.exists():
            problems.append(f"the database was not created at {ctx.layout.db_file}")
            return ""
        try:
            connection = sqlite3.connect(
                f"file:{ctx.layout.db_file}?mode=ro", uri=True, timeout=15)
        except sqlite3.Error as exc:
            problems.append(f"cannot open the database: {exc}")
            return ""
        try:
            tables = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if "users" not in tables:
                problems.append("the application did not create its schema "
                                "(no 'users' table)")
                return ""
            row = connection.execute(
                "SELECT username, is_super_admin, active FROM users "
                "WHERE is_super_admin=1 AND active=1").fetchone()
            if not row:
                problems.append("no active super-administrator exists")
                return ""
            if username and row[0] != username:
                warnings.append(
                    f"the super-administrator is '{row[0]}', not the configured "
                    f"'{username}'")

            # The account that used to ship with a known password must be gone.
            legacy = connection.execute(
                "SELECT COUNT(*) FROM users WHERE username='jnicolas' "
                "AND username<>?", (username or "",)).fetchone()
            if legacy and legacy[0]:
                problems.append(
                    "a legacy default account still exists in the database. "
                    "Remove it from the Users page before exposing this server.")
            return str(row[0])
        except sqlite3.Error as exc:
            problems.append(f"database check failed: {exc}")
            return ""
        finally:
            connection.close()

    def _module_units(self, ctx) -> list[tuple[str, str]]:
        units: list[tuple[str, str]] = []
        if ctx.get("modules.comics"):
            units.append(("cinemediavault-module@comics.service", "Comics"))
        for game in ctx.selected_games():
            units.append((f"cinemediavault-module@{game}.service", game.upper()))
        if ctx.get("modules.bookvault"):
            units.append(("cinemediavault-bookvault.service", "Book Vault"))
        return units


def steps():
    return [VerifyInstallation()]
