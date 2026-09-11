"""Install the CineMediaVault application payload.

The application is copied from the package's ``payload/`` tree into
``/opt/cinemediavault``. Two things happen beyond a plain copy:

1. **The default-administrator patch.** Upstream, the application upserts a
   hard-coded super-administrator with a known password every time it starts.
   That is acceptable on a single private host whose owner set it up by hand;
   it is not acceptable in an installer that anyone can run. The payload is
   patched so the bootstrap account comes from the root-owned
   ``admin-bootstrap.json`` the installer writes, and so no account is created
   at all when that file is absent.

2. **A manifest.** Every installed file is recorded with its SHA-256 so
   ``cinevaultctl verify`` can detect drift and ``upgrade`` can tell a
   customised file from an untouched one.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from ..core.errors import StepError
from ..core.fsops import copy_tree, sha256_file, write_file
from ..version import INSTALLER_VERSION
from .base import Step, StepResult

#: The upstream block that hard-codes a default administrator.
DEFAULT_ADMIN_MARKER = 'INSERT INTO users(username, full_name, email, password_hash'

BOOTSTRAP_REPLACEMENT = '''        now = auth_now()
        # CineMediaVault installer patch: the bootstrap administrator comes from
        # the root-owned admin-bootstrap.json the installer wrote, never from a
        # hard-coded default. With no bootstrap file and no existing account the
        # application starts with no users, and the login page says so, rather
        # than shipping a known password.
        _bootstrap_path = os.environ.get("CINEVAULT_ADMIN_BOOTSTRAP", "")
        _bootstrap = None
        if _bootstrap_path:
            try:
                with open(_bootstrap_path, "r", encoding="utf-8") as _bf:
                    _candidate = json.load(_bf)
                if isinstance(_candidate, dict) and _candidate.get("username") \\
                        and str(_candidate.get("password_hash", "")).startswith("pbkdf2_sha256$"):
                    _bootstrap = _candidate
            except (OSError, ValueError):
                _bootstrap = None
        if _bootstrap:
            conn.execute(
                """
                INSERT INTO users(username, full_name, email, password_hash, is_admin, is_super_admin, active, created_at, updated_at)
                VALUES(?, ?, ?, ?, 1, 1, 1, ?, ?)
                ON CONFLICT(username) DO UPDATE SET
                  is_admin=1,
                  is_super_admin=1,
                  active=1,
                  updated_at=excluded.updated_at
                """,
                (
                    str(_bootstrap.get("username")),
                    str(_bootstrap.get("full_name") or ""),
                    str(_bootstrap.get("email") or ""),
                    str(_bootstrap.get("password_hash")),
                    now,
                    now,
                ),
            )
'''


class InstallPayload(Step):
    id = "payload"
    title = "Application files"
    description = "Copy the CineMediaVault application into the installation root."
    depends_on = ("paths.install_root", "meta.installer_version")
    requires = ("directories",)

    def preview(self, ctx) -> str:
        source = ctx.package_root / "payload"
        count = sum(1 for _ in source.rglob("*") if _.is_file())
        return f"Install {count} application files into {ctx.layout.install_root}."

    def run(self, ctx) -> StepResult:
        source = ctx.package_root / "payload"
        if not source.is_dir():
            raise StepError(f"payload directory missing from the package: {source}",
                            step=self.id, recoverable=False)

        changed = 0
        for name, target in (
            ("app", ctx.layout.app_dir),
            ("scripts", ctx.layout.scripts_dir),
            ("creators", ctx.layout.creators_dir),
            ("assets", ctx.layout.assets_dir),
        ):
            directory = source / name
            if not directory.is_dir():
                continue
            changed += copy_tree(
                directory, target,
                mode_files=0o644, mode_dirs=0o755, mode_exec=0o755,
                user="root", group=ctx.service_group,
                backups=ctx.backups, dry_run=ctx.dry_run, logger=ctx.logger,
            )

        patched = self._patch_default_admin(ctx)
        manifest = self._write_manifest(ctx)

        return StepResult(
            changed=bool(changed or patched),
            summary=f"{changed} file(s) installed"
                    + (", default-administrator patch applied" if patched else ""),
            data={"files_changed": changed, "patched": patched,
                  "manifest_entries": manifest},
        )

    # ------------------------------------------------------------------
    def _patch_default_admin(self, ctx) -> bool:
        """Replace the hard-coded bootstrap administrator. Idempotent."""
        target = ctx.layout.app_dir / "cinemediavault.py"
        if ctx.dry_run:
            ctx.logger.info("[dry-run] would apply the default-administrator patch")
            return True
        if not target.is_file():
            raise StepError(f"application file missing: {target}", step=self.id)

        text = target.read_text(encoding="utf-8")
        if "CineMediaVault installer patch" in text:
            ctx.logger.debug("default-administrator patch already applied")
            return False

        # Match from "now = auth_now()" through the end of the users upsert.
        pattern = re.compile(
            r"[ \t]*now = auth_now\(\)\n"
            r"[ \t]*admin_hash = password_hash\([^\n]*\)\n"
            r"[ \t]*conn\.execute\(\s*\n"
            r'(?:.*?\n)*?'
            r"[ \t]*\)\n",
        )
        match = pattern.search(text)
        if not match or DEFAULT_ADMIN_MARKER not in match.group(0):
            raise StepError(
                "could not locate the default-administrator block in the "
                "application source. Refusing to install: the shipped application "
                "would create a super-administrator with a publicly known "
                "password. Re-stage the payload from a matching source version.",
                step=self.id, recoverable=False)

        patched = text[:match.start()] + BOOTSTRAP_REPLACEMENT + text[match.end():]
        # Compile before writing: a broken patch must never reach disk.
        try:
            compile(patched, str(target), "exec")
        except SyntaxError as exc:
            raise StepError(
                f"the default-administrator patch produced invalid Python "
                f"({exc}). Nothing was written.",
                step=self.id, recoverable=False) from exc

        write_file(target, patched, mode=0o644, user="root", group=ctx.service_group,
                   backups=ctx.backups, logger=ctx.logger)
        ctx.logger.success("removed the hard-coded default administrator account")
        return True

    def _write_manifest(self, ctx) -> int:
        if ctx.dry_run:
            return 0
        entries: dict[str, str] = {}
        for directory in (ctx.layout.app_dir, ctx.layout.scripts_dir,
                          ctx.layout.creators_dir, ctx.layout.assets_dir):
            if not directory.is_dir():
                continue
            for path in sorted(directory.rglob("*")):
                if path.is_file() and "__pycache__" not in path.parts:
                    entries[str(path.relative_to(ctx.layout.install_root))] = \
                        sha256_file(path)
        payload = {
            "installer_version": INSTALLER_VERSION,
            "installed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "files": entries,
        }
        write_file(ctx.layout.install_root / "payload-manifest.json",
                   json.dumps(payload, indent=2, sort_keys=True) + "\n",
                   mode=0o644, user="root", group=ctx.service_group,
                   backups=ctx.backups, logger=ctx.logger)
        return len(entries)


class CompileApplication(Step):
    id = "payload.compile"
    title = "Byte-compile check"
    description = "Verify every installed Python file parses before any service starts."
    requires = ("payload",)
    depends_on = ("paths.install_root",)

    def run(self, ctx) -> StepResult:
        if ctx.dry_run:
            return StepResult(summary="skipped in dry-run")
        targets = sorted(
            str(p) for p in ctx.layout.app_dir.rglob("*.py")
            if "__pycache__" not in p.parts)
        if not targets:
            raise StepError("no application Python files were installed", step=self.id)
        result = ctx.runner.run(
            ["python3", "-m", "py_compile", *targets], check=False, timeout=300)
        if result.returncode != 0:
            raise StepError(
                "the installed application does not compile:\n"
                + (result.stderr or result.stdout).strip()[-2000:],
                step=self.id, recoverable=False)
        return StepResult(changed=False,
                          summary=f"{len(targets)} application files compile cleanly")


def steps():
    return [InstallPayload(), CompileApplication()]
