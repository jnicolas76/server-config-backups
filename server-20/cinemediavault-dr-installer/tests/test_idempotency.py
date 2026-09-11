"""Idempotency, atomic writes, journalling and rollback.

These tests run the file-writing steps for real against a sandbox prefix, twice,
and assert that the second run changes nothing. That is the property the whole
"a second run repairs rather than duplicates" promise rests on.

Steps that need root - packages, users, systemd, firewall, service start - are
excluded here and covered by the mocked-flow tests instead.
"""

from __future__ import annotations

import json
import os
import stat
import unittest
from pathlib import Path

from tests.helpers import PACKAGE_ROOT, Sandbox, TEST_PASSWORD, hashed_config

from installer.core.fsops import (
    BackupSet, ensure_dir, new_backup_set, restore_backup_set, sha256_file,
    write_file,
)
from installer.core.journal import COMPLETED, Journal, fingerprint
from installer.engine import Engine
from installer.steps.base import ordered_steps

#: The steps that only touch the sandbox prefix and need no privileges.
UNPRIVILEGED_STEPS = (
    "directories", "directories.media", "payload", "payload.compile", "config",
    "database", "admin", "modules", "creators", "subtitles", "transcode",
)


def unprivileged_engine(ctx) -> Engine:
    steps = [s for s in ordered_steps() if s.id in UNPRIVILEGED_STEPS]
    return Engine(ctx, steps=steps)


class AtomicWrites(unittest.TestCase):
    def setUp(self):
        self.sandbox = Sandbox()
        self.addCleanup(self.sandbox.cleanup)
        self.target = self.sandbox.dir / "file.txt"

    def test_writing_identical_content_reports_no_change(self):
        self.assertTrue(write_file(self.target, "hello\n", mode=0o644))
        self.assertFalse(write_file(self.target, "hello\n", mode=0o644))

    def test_changing_content_reports_a_change(self):
        write_file(self.target, "hello\n", mode=0o644)
        self.assertTrue(write_file(self.target, "goodbye\n", mode=0o644))

    def test_changing_only_the_mode_reports_a_change(self):
        write_file(self.target, "hello\n", mode=0o644)
        self.assertTrue(write_file(self.target, "hello\n", mode=0o600))
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o600)

    def test_no_temporary_file_is_left_behind(self):
        write_file(self.target, "hello\n", mode=0o644)
        leftovers = [p.name for p in self.sandbox.dir.iterdir()
                     if p.name.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_the_previous_content_is_backed_up(self):
        write_file(self.target, "original\n", mode=0o644)
        backups = new_backup_set(self.sandbox.dir / "backups", "test")
        write_file(self.target, "replacement\n", mode=0o644, backups=backups)
        self.assertEqual(len(backups.entries), 1)
        saved = Path(backups.entries[0].backup)
        self.assertEqual(saved.read_text(encoding="utf-8"), "original\n")

    def test_a_new_file_is_recorded_as_previously_absent(self):
        backups = new_backup_set(self.sandbox.dir / "backups", "test")
        write_file(self.target, "new\n", mode=0o644, backups=backups)
        self.assertIsNone(backups.entries[0].backup)

    def test_restoring_puts_the_original_back(self):
        write_file(self.target, "original\n", mode=0o644)
        backups = new_backup_set(self.sandbox.dir / "backups", "test")
        write_file(self.target, "replacement\n", mode=0o644, backups=backups)
        restore_backup_set(backups.to_json())
        self.assertEqual(self.target.read_text(encoding="utf-8"), "original\n")

    def test_restoring_removes_a_file_that_did_not_exist_before(self):
        backups = new_backup_set(self.sandbox.dir / "backups", "test")
        write_file(self.target, "new\n", mode=0o644, backups=backups)
        restore_backup_set(backups.to_json())
        self.assertFalse(self.target.exists())

    def test_dry_run_writes_nothing(self):
        from installer.core.logging import InstallLogger
        write_file(self.target, "x\n", mode=0o644, dry_run=True,
                   logger=InstallLogger(console=False))
        self.assertFalse(self.target.exists())

    def test_ensure_dir_is_idempotent(self):
        target = self.sandbox.dir / "a" / "b" / "c"
        self.assertTrue(ensure_dir(target, mode=0o750))
        self.assertFalse(ensure_dir(target, mode=0o750))
        self.assertTrue(ensure_dir(target, mode=0o700))


class JournalBehaviour(unittest.TestCase):
    def setUp(self):
        self.sandbox = Sandbox()
        self.addCleanup(self.sandbox.cleanup)
        self.path = self.sandbox.dir / "journal.json"

    def test_a_completed_step_is_not_rerun(self):
        journal = Journal(self.path)
        journal.begin_run(installer_version="t", config_hash="h", order=["a"])
        mark = fingerprint("a", 1)
        self.assertTrue(journal.should_run("a", mark))
        journal.start_step("a", mark)
        journal.complete_step("a", summary="done")
        self.assertFalse(journal.should_run("a", mark))

    def test_a_changed_fingerprint_forces_a_rerun(self):
        journal = Journal(self.path)
        journal.begin_run(installer_version="t", config_hash="h", order=["a"])
        journal.start_step("a", fingerprint("a", 1))
        journal.complete_step("a")
        self.assertTrue(journal.should_run("a", fingerprint("a", 2)))

    def test_force_reruns_a_completed_step(self):
        journal = Journal(self.path)
        journal.begin_run(installer_version="t", config_hash="h", order=["a"])
        mark = fingerprint("a", 1)
        journal.start_step("a", mark)
        journal.complete_step("a")
        self.assertTrue(journal.should_run("a", mark, force=True))

    def test_a_step_interrupted_while_running_is_rerun(self):
        journal = Journal(self.path)
        journal.begin_run(installer_version="t", config_hash="h", order=["a"])
        mark = fingerprint("a", 1)
        journal.start_step("a", mark)
        # Simulate a power loss: the process dies without completing.
        reopened = Journal(self.path)
        self.assertTrue(reopened.should_run("a", mark))

    def test_it_survives_a_process_restart(self):
        journal = Journal(self.path)
        journal.begin_run(installer_version="t", config_hash="h", order=["a", "b"])
        journal.start_step("a", "mark-a")
        journal.complete_step("a", summary="first step done")
        reopened = Journal(self.path)
        self.assertEqual(reopened.record("a").status, COMPLETED)
        self.assertEqual(reopened.record("a").summary, "first step done")
        self.assertFalse(reopened.should_run("a", "mark-a"))

    def test_a_corrupt_journal_is_preserved_and_replaced(self):
        self.path.write_text("{not json", encoding="utf-8")
        journal = Journal(self.path)
        self.assertEqual(journal.order, [])
        corrupt = list(self.sandbox.dir.glob("journal.corrupt-*.json"))
        self.assertEqual(len(corrupt), 1)

    def test_the_journal_is_not_world_readable(self):
        journal = Journal(self.path)
        journal.begin_run(installer_version="t", config_hash="h", order=["a"])
        mode = stat.S_IMODE(self.path.stat().st_mode)
        self.assertEqual(mode & 0o077, 0, f"journal is readable by others: {mode:04o}")


class RealInstallIsIdempotent(unittest.TestCase):
    """Run the unprivileged steps for real, twice."""

    def setUp(self):
        self.sandbox = Sandbox()
        self.addCleanup(self.sandbox.cleanup)
        self.config = hashed_config(self.sandbox)

    def _run(self):
        ctx = self.sandbox.context(config=json.loads(json.dumps(self.config)))
        return ctx, unprivileged_engine(ctx).run()

    def test_first_run_succeeds_and_second_changes_nothing(self):
        ctx, first = self._run()
        self.assertTrue(first.ok, first.error)
        self.assertTrue(first.changed, "the first run should change things")

        _, second = self._run()
        self.assertTrue(second.ok, second.error)
        self.assertEqual(
            second.changed, [],
            f"a second run changed: {second.changed}. Every step must converge.")
        self.assertGreater(len(second.skipped), 0,
                           "the second run should skip completed steps")

    def test_file_contents_are_byte_identical_across_runs(self):
        ctx, _ = self._run()
        tracked = sorted(
            p for p in ctx.layout.install_root.rglob("*")
            if p.is_file() and "__pycache__" not in p.parts)
        tracked += sorted(p for p in ctx.layout.config_root.rglob("*") if p.is_file())
        before = {str(p): sha256_file(p) for p in tracked}

        self._run()

        after = {path: sha256_file(Path(path)) for path in before}
        differing = [p for p in before if before[p] != after[p]]
        self.assertEqual(differing, [], f"rewritten on a second run: {differing}")

    def test_forcing_a_step_reruns_it_without_breaking_anything(self):
        ctx, _ = self._run()
        forced = self.sandbox.context(config=json.loads(json.dumps(self.config)))
        forced.force_steps = ("config",)
        report = unprivileged_engine(forced).run()
        self.assertTrue(report.ok, report.error)
        self.assertIn("config", report.executed)

    def test_the_journal_records_every_step(self):
        ctx, report = self._run()
        journal = Journal(ctx.layout.journal_file)
        for step_id in report.executed:
            self.assertEqual(journal.record(step_id).status, COMPLETED, step_id)


class GeneratedFilePermissions(unittest.TestCase):
    def setUp(self):
        self.sandbox = Sandbox()
        self.addCleanup(self.sandbox.cleanup)
        ctx = self.sandbox.context(config=hashed_config(self.sandbox))
        report = unprivileged_engine(ctx).run()
        self.assertTrue(report.ok, report.error)
        self.ctx = ctx

    def test_secret_files_are_not_world_readable(self):
        for path in (self.ctx.layout.secrets_file,
                     self.ctx.layout.env_file,
                     self.ctx.layout.config_file,
                     self.ctx.layout.admin_bootstrap_file):
            if not path.exists():
                continue
            mode = stat.S_IMODE(path.stat().st_mode)
            self.assertEqual(mode & 0o007, 0,
                             f"{path} is world-accessible ({mode:04o})")

    def test_the_admin_password_is_nowhere_on_disk(self):
        for root in (self.ctx.layout.install_root, self.ctx.layout.config_root,
                     self.ctx.layout.state_root):
            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                try:
                    content = path.read_bytes()
                except OSError:
                    continue
                self.assertNotIn(TEST_PASSWORD.encode(), content,
                                 f"the password appears in {path}")

    def test_the_bootstrap_file_holds_only_a_hash(self):
        path = self.ctx.layout.admin_bootstrap_file
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertTrue(payload["password_hash"].startswith("pbkdf2_sha256$"))
        self.assertEqual(payload["username"], "tester")
        self.assertNotIn("password", {k for k in payload if k != "password_hash"})

    def test_the_default_administrator_was_removed_from_the_application(self):
        source = (self.ctx.layout.app_dir / "cinemediavault.py").read_text(
            encoding="utf-8")
        self.assertIn("CineMediaVault installer patch", source)
        self.assertNotIn('password_hash("admin1")', source)
        self.assertNotIn("VALUES('jnicolas'", source)

    def test_the_patched_application_still_compiles(self):
        import py_compile
        import tempfile
        target = self.ctx.layout.app_dir / "cinemediavault.py"
        with tempfile.TemporaryDirectory() as tmp:
            py_compile.compile(str(target), cfile=str(Path(tmp) / "out.pyc"),
                               doraise=True)

    def test_the_account_theme_feature_survives_patching(self):
        """The per-user theme system (default/royal-blue/green) must reach a
        clean install intact: the migration, the strict allow-list, the
        account route, and the shared theme module all need to be present in
        the installed application, not just on the source machine."""
        source = (self.ctx.layout.app_dir / "cinemediavault.py").read_text(
            encoding="utf-8")
        self.assertIn("ALTER TABLE users ADD COLUMN theme", source)
        self.assertIn("theme TEXT NOT NULL DEFAULT 'default'", source)
        self.assertIn("THEME_ALLOWED", source)
        self.assertIn('if path == "/account":', source)
        self.assertIn('if path == "/account/theme":', source)
        theme_module = self.ctx.layout.app_dir / "cinevault_theme.py"
        self.assertTrue(theme_module.is_file(), "cinevault_theme.py was not installed")
        theme_source = theme_module.read_text(encoding="utf-8")
        self.assertIn('THEME_ALLOWED = ("default", "royal-blue", "green")', theme_source)
        theme_css = self.ctx.layout.assets_dir / "cinevault-theme.css"
        self.assertTrue(theme_css.is_file(), "cinevault-theme.css was not installed")
        css_source = theme_css.read_text(encoding="utf-8")
        self.assertIn('html[data-theme="royal-blue"]', css_source)
        self.assertIn('html[data-theme="green"]', css_source)

    def test_the_usage_analytics_feature_survives_patching(self):
        """The bandwidth/CPU usage analytics system (Active & Historical Usage
        admin page, its APIs, and the shared cinevault_usage module) must reach
        a clean install intact, and the movie/TV/music modules that each carry
        their own copy of cinevault_usage.py (the same convention as
        cinevault_theme.py) must all have it too."""
        source = (self.ctx.layout.app_dir / "cinemediavault.py").read_text(
            encoding="utf-8")
        self.assertIn("import cinevault_usage", source)
        self.assertIn('<h1>Active &amp; Historical Usage</h1>', source)
        self.assertIn('if path == "/admin/usage":', source)
        self.assertIn('if path == "/api/admin/usage/active":', source)
        self.assertIn('if path == "/api/admin/usage/history":', source)
        self.assertIn('if path == "/api/admin/usage/users":', source)
        self.assertIn('if path == "/admin/usage/retention":', source)
        self.assertIn("cinevault_usage.start_background_thread()", source)
        for relative in ("cinevault_usage.py",
                          "media-download-library/cinevault_usage.py",
                          "tv-download-library/cinevault_usage.py"):
            module = self.ctx.layout.app_dir / relative
            self.assertTrue(module.is_file(), f"{relative} was not installed")
            module_source = module.read_text(encoding="utf-8")
            self.assertIn("CREATE TABLE IF NOT EXISTS usage_system_interval", module_source)
            self.assertIn("CREATE TABLE IF NOT EXISTS usage_breakdown_interval", module_source)
            self.assertIn("def record_bytes(", module_source)
        for relative in ("media-download-library/media_download_server.py",
                          "tv-download-library/tv_download_server.py",
                          "music_module.py"):
            module_source = (self.ctx.layout.app_dir / relative).read_text(
                encoding="utf-8")
            self.assertIn("cinevault_usage.session_start(", module_source)
            self.assertIn("cinevault_usage.record_bytes(", module_source)

    def test_a_payload_manifest_was_written(self):
        manifest = json.loads(
            (self.ctx.layout.install_root / "payload-manifest.json")
            .read_text(encoding="utf-8"))
        self.assertGreater(len(manifest["files"]), 20)
        self.assertIn("app/cinemediavault.py", manifest["files"])

    def test_the_manifest_hashes_match_what_is_installed(self):
        manifest = json.loads(
            (self.ctx.layout.install_root / "payload-manifest.json")
            .read_text(encoding="utf-8"))
        for relative, expected in list(manifest["files"].items())[:15]:
            path = self.ctx.layout.install_root / relative
            self.assertEqual(sha256_file(path), expected, relative)


if __name__ == "__main__":
    unittest.main()
