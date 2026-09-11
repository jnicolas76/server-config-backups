"""Create the dedicated, unprivileged service account.

CineMediaVault runs as a system user with no login shell, no home directory it
writes to, and membership only in the groups it needs (``video``/``render`` for
hardware transcoding, ``docker`` is deliberately *not* granted: the service
never talks to Docker).
"""

from __future__ import annotations

import grp
import pwd

from ..core.errors import StepError
from .base import Step, StepResult

#: Groups the service joins when they exist and the feature needs them.
OPTIONAL_GROUPS = {
    "video": "hardware video devices",
    "render": "VA-API render nodes",
}


class CreateServiceUser(Step):
    id = "users"
    title = "Service account"
    description = ("Create the unprivileged 'cinevault' system user and group "
                   "that own the application state.")
    depends_on = ("paths.service_user", "paths.service_group",
                  "transcode.hls_enabled", "transcode.hls_encoder")

    def preview(self, ctx) -> str:
        user = ctx.service_user
        if _user_exists(user):
            return f"Service user '{user}' already exists; group membership will be checked."
        return f"Create system user and group '{user}' (no shell, no password)."

    def run(self, ctx) -> StepResult:
        user = ctx.service_user
        group = ctx.service_group
        changed = False
        notes: list[str] = []

        if not _group_exists(group):
            ctx.runner.run(["groupadd", "--system", group])
            changed = True
            notes.append(f"created group {group}")

        if not _user_exists(user):
            ctx.runner.run([
                "useradd",
                "--system",
                "--gid", group,
                "--home-dir", str(ctx.layout.state_root),
                "--no-create-home",
                "--shell", "/usr/sbin/nologin",
                "--comment", "CineMediaVault service account",
                user,
            ])
            changed = True
            notes.append(f"created user {user}")
        else:
            notes.append(f"user {user} already exists")

        # A pre-existing account with a login shell is a security regression we
        # will not silently accept, but we also will not modify an account the
        # operator may be using for something else. Report it instead.
        if not ctx.dry_run and _user_exists(user):
            entry = pwd.getpwnam(user)
            if entry.pw_shell not in ("/usr/sbin/nologin", "/sbin/nologin", "/bin/false"):
                return StepResult(changed=changed, summary="; ".join(notes)).warn(
                    f"Existing account '{user}' has an interactive shell "
                    f"({entry.pw_shell}). CineMediaVault does not need one; "
                    f"consider 'usermod -s /usr/sbin/nologin {user}'.")

        # Hardware-transcode groups, only when they could actually be used.
        if ctx.get("transcode.hls_enabled"):
            for extra, why in OPTIONAL_GROUPS.items():
                if not _group_exists(extra):
                    continue
                if _in_group(user, extra):
                    continue
                if ctx.dry_run:
                    notes.append(f"would add {user} to {extra}")
                    continue
                ctx.runner.run(["usermod", "-aG", extra, user])
                changed = True
                notes.append(f"added {user} to {extra} ({why})")

        return StepResult(changed=changed, summary="; ".join(notes),
                          data={"user": user, "group": group})


def _user_exists(name: str) -> bool:
    try:
        pwd.getpwnam(name)
        return True
    except KeyError:
        return False


def _group_exists(name: str) -> bool:
    try:
        grp.getgrnam(name)
        return True
    except KeyError:
        return False


def _in_group(user: str, group: str) -> bool:
    try:
        return user in grp.getgrnam(group).gr_mem
    except KeyError:
        return False


def steps():
    return [CreateServiceUser()]
