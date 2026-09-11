"""Create the administrator bootstrap file.

The plaintext password never reaches this step. The wizard and the CLI hash it
the moment it is validated and immediately overwrite the plaintext in memory;
what arrives here is only ``admin.password_hash``.
"""

from __future__ import annotations

import sqlite3

from ..config.secrets import AdminBootstrap
from ..core.errors import StepError
from .base import Step, StepResult


class CreateAdminBootstrap(Step):
    id = "admin"
    title = "Administrator account"
    description = ("Write the root-owned bootstrap file the application uses to "
                   "create the super-administrator on first start.")
    depends_on = ("admin.username", "admin.full_name", "admin.email")
    requires = ("config", "database")

    def preview(self, ctx) -> str:
        return (f"Create super-administrator '{ctx.get('admin.username')}'. "
                f"The password is stored only as a PBKDF2-SHA256 hash.")

    def run(self, ctx) -> StepResult:
        username = (ctx.get("admin.username") or "").strip()
        password_hash = ctx.get("admin.password_hash") or ""

        if not username:
            raise StepError("no administrator username was configured", step=self.id,
                            recoverable=False)

        if not password_hash.startswith("pbkdf2_sha256$"):
            # An existing installation being repaired already has the account;
            # only a fresh install genuinely needs the hash.
            if self._account_exists(ctx, username):
                return StepResult(
                    changed=False,
                    summary=f"administrator '{username}' already exists; "
                            f"password unchanged")
            raise StepError(
                "no administrator password hash is available and no account "
                "exists yet. Re-run the wizard, or use "
                "`cinevaultctl admin set-password`.",
                step=self.id, recoverable=False)

        bootstrap = AdminBootstrap(ctx.layout.admin_bootstrap_file,
                                   group=ctx.service_group)
        changed = bootstrap.write(
            username=username,
            password_hash=password_hash,
            full_name=ctx.get("admin.full_name") or "",
            email=ctx.get("admin.email") or "",
            backups=ctx.backups,
            dry_run=ctx.dry_run,
        )
        return StepResult(
            changed=changed,
            summary=f"bootstrap written for '{username}'",
            data={"username": username},
        )

    def _account_exists(self, ctx, username: str) -> bool:
        if not ctx.layout.db_file.exists():
            return False
        try:
            connection = sqlite3.connect(
                f"file:{ctx.layout.db_file}?mode=ro", uri=True, timeout=10)
            try:
                row = connection.execute(
                    "SELECT 1 FROM users WHERE username=? AND is_super_admin=1",
                    (username,)).fetchone()
                return row is not None
            finally:
                connection.close()
        except sqlite3.Error:
            return False


def steps():
    return [CreateAdminBootstrap()]
