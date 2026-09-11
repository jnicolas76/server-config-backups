"""Shared test fixtures.

The tests build a complete, valid configuration rooted in a temporary
directory, so the whole installer can be exercised without root and without
touching anything outside that directory.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))

from installer.config.schema import apply_defaults          # noqa: E402
from installer.core.context import Context                  # noqa: E402
from installer.core.logging import InstallLogger            # noqa: E402

TEST_PASSWORD = "Correct-Horse-9-Battery"
TEST_TMDB_KEY = "tmdb-test-key-0123456789abcdef"
TEST_WEBEX = "https://webexapis.com/v1/webhooks/incoming/TESTTOKENVALUE123456"


class Sandbox:
    """A temporary filesystem holding a full, plausible installation."""

    def __init__(self):
        self.dir = Path(tempfile.mkdtemp(prefix="cmv-test-"))
        self.prefix = self.dir / "prefix"
        self.media = self.dir / "media"
        for name in ("Movies", "TV", "Music", "Books", "Comics", "Games",
                     "Recordings"):
            path = self.media / name
            path.mkdir(parents=True, exist_ok=True)
            # Non-empty, so the "looks unmounted" heuristic does not fire.
            (path / ".keep").write_text("test\n", encoding="utf-8")
        self.prefix.mkdir(parents=True, exist_ok=True)

    def cleanup(self) -> None:
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def config(self, **overrides) -> dict:
        import grp
        import pwd
        user = pwd.getpwuid(os.getuid()).pw_name
        group = grp.getgrgid(os.getgid()).gr_name
        config = {
            "meta": {"instance_name": "Test Vault"},
            "deployment": {"mode": "standard", "timezone": "America/Denver",
                           "accept_licenses": True},
            "admin": {"username": "tester", "full_name": "Test User",
                      "email": "tester@example.com", "password": TEST_PASSWORD},
            "network": {
                "hostname": "testvault.local", "listen_address": "0.0.0.0",
                "https_port": 15443, "tls_mode": "self_signed",
                "enable_http": False,
                "trusted_networks": ["192.168.0.0/16"],
                "configure_firewall": False,
            },
            "media": {
                "movies_root": str(self.media / "Movies"),
                "tv_root": str(self.media / "TV"),
                "music_root": str(self.media / "Music"),
                "books_root": str(self.media / "Books"),
                "comics_root": str(self.media / "Comics"),
                "comic_library_root": str(self.media / "comic-library"),
                "games_root": str(self.media / "Games"),
                "recordings_root": str(self.media / "Recordings"),
                "create_missing": True,
                "mounts": [],
            },
            "metadata": {"tmdb_api_key": TEST_TMDB_KEY},
            "livetv": {"enabled": False},
            "epg": {"mode": "device_only"},
            "dvr": {"enabled": False},
            "subtitles": {"use_existing": True, "languages": ["en"]},
            "integrations": {"webex_webhook_url": TEST_WEBEX},
            "modules": {
                "movies": True, "tv": True, "music": True, "comics": True,
                "bookvault": True, "games": ["nes", "sega"],
                "creators": ["comics", "games"],
            },
            "paths": {
                "install_root": str(self.prefix / "opt/cinemediavault"),
                "config_root": str(self.prefix / "etc/cinemediavault"),
                "state_root": str(self.prefix / "var/lib/cinemediavault"),
                "log_root": str(self.prefix / "var/log/cinemediavault"),
                "cache_root": str(self.prefix / "var/cache/cinemediavault"),
                "service_user": user,
                "service_group": group,
            },
        }
        for section, values in overrides.items():
            config.setdefault(section, {}).update(values)
        return apply_defaults(config)

    def context(self, *, dry_run: bool = False, config: dict | None = None,
                **overrides) -> Context:
        logger = InstallLogger(console=False)
        ctx = Context(config or self.config(**overrides), logger=logger,
                      dry_run=dry_run, package_root=PACKAGE_ROOT, assume_yes=True)
        ctx.detect_facts()
        return ctx


def hashed_config(sandbox: Sandbox, **overrides) -> dict:
    """A configuration whose admin password has already been hashed."""
    from installer.config.schema import put
    from installer.config.secrets import hash_password

    config = sandbox.config(**overrides)
    put(config, "admin.password_hash", hash_password(TEST_PASSWORD))
    put(config, "admin.password", "")
    return config
