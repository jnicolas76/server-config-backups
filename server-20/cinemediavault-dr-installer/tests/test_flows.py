"""End-to-end flows: dry run, failure and rollback, backup/restore, upgrade,
the wizard API, and the CLI surface.

Privileged operations are exercised through a runner that records commands
instead of executing them, so the whole decision path is tested without needing
root or altering the machine.
"""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

from tests.helpers import (
    PACKAGE_ROOT, Sandbox, TEST_PASSWORD, TEST_TMDB_KEY, hashed_config,
)

from installer.core.errors import StepError
from installer.core.runner import Result, Runner
from installer.engine import Engine
from installer.steps.base import Step, StepResult, ordered_steps
from tests.test_idempotency import UNPRIVILEGED_STEPS, unprivileged_engine


class RecordingRunner(Runner):
    """A Runner that records commands rather than running them.

    Read-only probes still execute, because the steps legitimately branch on
    what they find - which package is installed, whether a unit is active - and
    faking those would test a different program.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.commands: list[list[str]] = []
        self.responses: dict[str, Result] = {}

    def run(self, argv, *, allow_in_dry_run=False, **kwargs):
        argv = [str(a) for a in argv]
        if allow_in_dry_run:
            return super().run(argv, allow_in_dry_run=True, **kwargs)
        if self.dry_run:
            # Honour dry-run exactly as the real runner does, so a test that
            # asserts "a dry run executes nothing" is testing the installer
            # rather than this mock.
            return Result(argv, 0, "", "", 0.0)
        self.commands.append(argv)
        key = " ".join(argv[:2])
        if key in self.responses:
            return self.responses[key]
        return Result(argv, 0, "", "", 0.0)

    def ran(self, *fragments: str) -> bool:
        return any(all(f in " ".join(c) for f in fragments) for c in self.commands)


class DryRunChangesNothing(unittest.TestCase):
    def setUp(self):
        self.sandbox = Sandbox()
        self.addCleanup(self.sandbox.cleanup)

    def test_a_full_dry_run_creates_no_file(self):
        ctx = self.sandbox.context(config=hashed_config(self.sandbox), dry_run=True)
        ctx.runner = RecordingRunner(dry_run=True, logger=ctx.logger)
        before = set(self.sandbox.prefix.rglob("*"))
        report = Engine(ctx).run()
        after = set(self.sandbox.prefix.rglob("*"))
        self.assertTrue(report.ok, report.error)
        self.assertEqual(before, after, "a dry run must not create anything")

    def test_a_dry_run_runs_no_command(self):
        ctx = self.sandbox.context(config=hashed_config(self.sandbox), dry_run=True)
        recorder = RecordingRunner(dry_run=True, logger=ctx.logger)
        ctx.runner = recorder
        Engine(ctx).run()
        self.assertEqual(recorder.commands, [],
                         f"a dry run executed: {recorder.commands}")

    def test_a_dry_run_touches_no_media(self):
        ctx = self.sandbox.context(config=hashed_config(self.sandbox), dry_run=True)
        ctx.runner = RecordingRunner(dry_run=True, logger=ctx.logger)
        movies = Path(ctx.get("media.movies_root"))
        before = sorted(p.name for p in movies.iterdir())
        Engine(ctx).run()
        self.assertEqual(sorted(p.name for p in movies.iterdir()), before)


class MockedFullInstall(unittest.TestCase):
    """Every step runs, with privileged commands recorded rather than executed."""

    def setUp(self):
        self.sandbox = Sandbox()
        self.addCleanup(self.sandbox.cleanup)
        self.ctx = self.sandbox.context(config=hashed_config(self.sandbox))
        self.recorder = RecordingRunner(logger=self.ctx.logger)
        self.ctx.runner = self.recorder

    def _fresh_account_context(self):
        """A context whose service account genuinely does not exist yet."""
        config = hashed_config(self.sandbox)
        config["paths"]["service_user"] = "cmv-test-nonexistent"
        config["paths"]["service_group"] = "cmv-test-nonexistent"
        ctx = self.sandbox.context(config=config)
        ctx.runner = RecordingRunner(logger=ctx.logger)
        return ctx

    def test_the_privileged_steps_issue_the_expected_commands(self):
        self.ctx = self._fresh_account_context()
        self.recorder = self.ctx.runner
        steps = [s for s in ordered_steps()
                 if s.id in ("packages", "users", "timers")]
        report = Engine(self.ctx, steps=steps).run()
        self.assertTrue(report.ok, report.error)
        self.assertTrue(self.recorder.ran("apt-get", "install"))
        self.assertTrue(self.recorder.ran("useradd", "--system"))
        self.assertTrue(self.recorder.ran("systemctl", "enable"))

    def test_the_service_account_gets_no_shell(self):
        self.ctx = self._fresh_account_context()
        self.recorder = self.ctx.runner
        steps = [s for s in ordered_steps() if s.id == "users"]
        Engine(self.ctx, steps=steps).run()
        useradd = next(c for c in self.recorder.commands if c[0] == "useradd")
        self.assertIn("--shell", useradd)
        self.assertIn("/usr/sbin/nologin", useradd)
        self.assertIn("--system", useradd)

    def test_the_service_account_is_not_added_to_docker(self):
        self.ctx = self._fresh_account_context()
        self.recorder = self.ctx.runner
        steps = [s for s in ordered_steps() if s.id == "users"]
        Engine(self.ctx, steps=steps).run()
        for command in self.recorder.commands:
            if command[0] == "usermod":
                self.assertNotIn("docker", command)

    def test_the_bulk_transcode_queue_is_never_started(self):
        config = hashed_config(
            self.sandbox,
            transcode={"library_queue_enabled": True,
                       "library_queue_autostart": True, "hls_enabled": True})
        # autostart with enabled is refused by validation; force the unsafe
        # combination directly to prove the step still refuses to arm it.
        ctx = self.sandbox.context(config=config)
        ctx.runner = RecordingRunner(logger=ctx.logger)
        steps = [s for s in ordered_steps() if s.id == "transcode"]
        report = Engine(ctx, steps=steps).run()
        self.assertTrue(report.ok, report.error)
        settings = json.loads(
            (ctx.layout.state_root / "transcode" / "queue-settings.json")
            .read_text(encoding="utf-8"))
        self.assertEqual(settings["state"], "stopped")
        self.assertFalse(settings["autostart"])
        self.assertEqual(settings["queue"], [])
        self.assertTrue(any("REWRITES SOURCE MEDIA" in w.upper()
                            or "rewrites source media" in w
                            for w in report.warnings))


class FailureAndRollback(unittest.TestCase):
    def setUp(self):
        self.sandbox = Sandbox()
        self.addCleanup(self.sandbox.cleanup)

    def test_a_failing_step_stops_the_run_and_records_it(self):
        class Exploding(Step):
            id = "explode"
            title = "Exploding step"

            def run(self, ctx):
                raise StepError("deliberate failure", step=self.id)

        ctx = self.sandbox.context(config=hashed_config(self.sandbox))
        steps = [s for s in ordered_steps() if s.id in ("directories", "config")]
        steps.append(Exploding())
        report = Engine(ctx, steps=steps).run()
        self.assertFalse(report.ok)
        self.assertEqual(report.failed_step, "explode")
        self.assertIn("deliberate failure", report.error)
        self.assertIn("directories", report.executed)

    def test_rollback_restores_replaced_files(self):
        from installer.tooling.rollback import rollback

        ctx = self.sandbox.context(config=hashed_config(self.sandbox))
        unprivileged_engine(ctx).run()
        config_file = ctx.layout.config_file
        original = config_file.read_text(encoding="utf-8")
        self.assertIn("Test Vault", original)

        # A second run with a different name replaces the file...
        changed = hashed_config(self.sandbox, meta={"instance_name": "Renamed"})
        ctx2 = self.sandbox.context(config=changed)
        unprivileged_engine(ctx2).run()
        self.assertIn("Renamed", config_file.read_text(encoding="utf-8"))

        # ...and rolling that run back restores the previous content.
        ctx3 = self.sandbox.context(config=changed)
        ctx3.runner = RecordingRunner(logger=ctx3.logger)
        rollback(ctx3)
        self.assertIn("Test Vault", config_file.read_text(encoding="utf-8"))

    def test_rollback_in_dry_run_restores_nothing(self):
        from installer.tooling.rollback import rollback

        ctx = self.sandbox.context(config=hashed_config(self.sandbox))
        unprivileged_engine(ctx).run()
        ctx2 = self.sandbox.context(config=hashed_config(self.sandbox), dry_run=True)
        ctx2.runner = RecordingRunner(dry_run=True, logger=ctx2.logger)
        result = rollback(ctx2)
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["restored"], 0)
        self.assertTrue(ctx.layout.config_file.exists())


class UninstallSafety(unittest.TestCase):
    def setUp(self):
        self.sandbox = Sandbox()
        self.addCleanup(self.sandbox.cleanup)
        self.ctx = self.sandbox.context(config=hashed_config(self.sandbox))
        unprivileged_engine(self.ctx).run()
        self.recorder = RecordingRunner(logger=self.ctx.logger)
        self.ctx.runner = self.recorder

    def test_media_survives_a_full_uninstall(self):
        from installer.tooling.rollback import uninstall

        movies = Path(self.ctx.get("media.movies_root"))
        marker = movies / "important-film.mkv"
        marker.write_text("pretend video\n", encoding="utf-8")

        uninstall(self.ctx, remove_media=False, remove_state=True)

        self.assertTrue(marker.exists(), "uninstall deleted media")
        self.assertTrue(movies.is_dir())

    def test_source_media_survives_even_when_removal_is_requested(self):
        from installer.tooling.rollback import uninstall

        movies = Path(self.ctx.get("media.movies_root"))
        marker = movies / "important-film.mkv"
        marker.write_text("pretend video\n", encoding="utf-8")
        recordings = Path(self.ctx.get("media.recordings_root"))
        recording = recordings / "recorded.ts"
        recording.write_text("pretend recording\n", encoding="utf-8")

        result = uninstall(self.ctx, remove_media=True, remove_state=True)

        self.assertTrue(marker.exists(),
                        "even --remove-generated-media must not delete a source library")
        self.assertTrue(recording.exists(),
                        "recordings are the user's own media and must survive")
        self.assertNotIn(str(movies), result["media_removed"])

    def test_state_is_kept_by_default(self):
        from installer.tooling.rollback import uninstall

        database = self.ctx.layout.db_file
        self.assertTrue(database.exists())
        uninstall(self.ctx, remove_media=False, remove_state=False)
        self.assertTrue(database.exists(), "the database was removed without being asked")

    def test_uninstall_removes_the_application_tree(self):
        from installer.tooling.rollback import uninstall

        install_root = self.ctx.layout.install_root
        self.assertTrue(install_root.exists())
        uninstall(self.ctx, remove_media=False, remove_state=False)
        self.assertFalse(install_root.exists())

    def test_uninstall_stops_units_before_removing_them(self):
        from installer.tooling.rollback import uninstall

        uninstall(self.ctx, remove_media=False, remove_state=False)
        self.assertTrue(self.recorder.ran("systemctl", "disable"))
        self.assertTrue(self.recorder.ran("systemctl", "daemon-reload"))


class BackupAndRestore(unittest.TestCase):
    def setUp(self):
        self.sandbox = Sandbox()
        self.addCleanup(self.sandbox.cleanup)
        self.ctx = self.sandbox.context(config=hashed_config(self.sandbox))
        report = unprivileged_engine(self.ctx).run()
        self.assertTrue(report.ok, report.error)
        self.ctx.runner = RecordingRunner(logger=self.ctx.logger)

    def test_a_backup_can_be_created_and_inspected(self):
        from installer.tooling import backup

        archive = backup.create(self.ctx, label="test")
        self.assertTrue(archive.is_file())
        manifest = backup.inspect(archive)
        self.assertIn("db/cinemediavault.db", manifest["contents"])
        self.assertIn("config/cinemediavault.yaml", manifest["contents"])

    def test_a_backup_excludes_media(self):
        import tarfile
        from installer.tooling import backup

        marker = Path(self.ctx.get("media.movies_root")) / "film.mkv"
        marker.write_text("video\n", encoding="utf-8")
        archive = backup.create(self.ctx, label="test")
        with tarfile.open(archive, "r:gz") as tar:
            names = tar.getnames()
        self.assertFalse([n for n in names if "film.mkv" in n])

    def test_a_backup_excludes_the_tls_private_key(self):
        import tarfile
        from installer.tooling import backup

        archive = backup.create(self.ctx, label="test")
        with tarfile.open(archive, "r:gz") as tar:
            names = tar.getnames()
        self.assertFalse([n for n in names if n.endswith(".key")],
                         "a backup must never carry the private key")

    def test_a_backup_without_secrets_omits_them(self):
        import tarfile
        from installer.tooling import backup

        archive = backup.create(self.ctx, include_secrets=False, label="nosecrets")
        with tarfile.open(archive, "r:gz") as tar:
            names = tar.getnames()
            self.assertFalse([n for n in names if n.endswith("secrets.env")])

    def test_a_backup_containing_secrets_is_private(self):
        import stat
        from installer.tooling import backup

        archive = backup.create(self.ctx, include_secrets=True, label="secrets")
        mode = stat.S_IMODE(archive.stat().st_mode)
        self.assertEqual(mode & 0o077, 0, f"backup is readable by others: {mode:04o}")

    def test_restore_refuses_an_archive_that_escapes_its_destination(self):
        import tarfile
        import tempfile
        from installer.core.errors import InstallerError
        from installer.tooling.backup import _safe_extract

        with tempfile.TemporaryDirectory() as tmp:
            evil = Path(tmp) / "evil.tar"
            payload = Path(tmp) / "payload"
            payload.write_text("x", encoding="utf-8")
            with tarfile.open(evil, "w") as tar:
                tar.add(payload, arcname="../../escaped")
            destination = Path(tmp) / "dest"
            destination.mkdir()
            with tarfile.open(evil, "r") as tar:
                with self.assertRaises(InstallerError):
                    _safe_extract(tar, destination)


class ConfigurationMigration(unittest.TestCase):
    def test_a_schemaless_configuration_is_migrated(self):
        from installer.tooling.upgrade import current_schema_version, migrate
        from installer.version import CONFIG_SCHEMA_VERSION

        legacy = {"MOVIE_ROOT": "/srv/media/Movies", "TV_ROOT": "/srv/media/TV"}
        migrated, applied = migrate(legacy)
        self.assertEqual(current_schema_version(migrated), CONFIG_SCHEMA_VERSION)
        self.assertTrue(applied)
        from installer.config.schema import get
        self.assertEqual(get(migrated, "media.movies_root"), "/srv/media/Movies")
        self.assertEqual(get(migrated, "media.tv_root"), "/srv/media/TV")

    def test_a_current_configuration_needs_no_migration(self):
        from installer.tooling.upgrade import migrate
        sandbox = Sandbox()
        self.addCleanup(sandbox.cleanup)
        _migrated, applied = migrate(sandbox.config())
        self.assertEqual(applied, [])

    def test_a_newer_schema_is_refused(self):
        from installer.core.errors import ConfigError
        from installer.tooling.upgrade import migrate
        with self.assertRaises(ConfigError):
            migrate({"meta": {"schema_version": 999}})


class WizardApi(unittest.TestCase):
    def setUp(self):
        self.sandbox = Sandbox()
        self.addCleanup(self.sandbox.cleanup)
        from wizard.session import WizardState
        self.state = WizardState(self.sandbox.dir / "setup-state.json")

    def test_a_stage_only_accepts_its_own_fields(self):
        from wizard import api
        api.submit_stage(self.state, "welcome", {
            "meta.instance_name": "Vault",
            "deployment.timezone": "UTC",
            "deployment.accept_licenses": True,
            # Not part of this stage; must be ignored rather than applied.
            "network.https_port": 9999,
            "paths.install_root": "/tmp/evil",
        })
        from installer.config.schema import get
        self.assertNotEqual(get(self.state.config, "network.https_port"), 9999)
        self.assertNotEqual(get(self.state.config, "paths.install_root"), "/tmp/evil")

    def test_an_unknown_stage_is_rejected(self):
        from wizard import api
        with self.assertRaises(api.ApiError):
            api.submit_stage(self.state, "not-a-stage", {})

    def test_the_password_is_hashed_and_the_plaintext_discarded(self):
        from wizard import api
        from installer.config.schema import get

        api.submit_stage(self.state, "welcome",
                         {"deployment.timezone": "UTC",
                          "deployment.accept_licenses": True})
        api.submit_stage(self.state, "admin",
                         {"admin.username": "tester", "admin.password": TEST_PASSWORD})

        self.assertEqual(get(self.state.config, "admin.password"), "")
        self.assertTrue(
            get(self.state.config, "admin.password_hash").startswith("pbkdf2_sha256$"))
        self.assertNotIn(TEST_PASSWORD,
                         self.state.path.read_text(encoding="utf-8"))

    def test_a_weak_password_is_rejected(self):
        from wizard import api
        with self.assertRaises(api.ApiError) as caught:
            api.submit_stage(self.state, "admin",
                             {"admin.username": "tester", "admin.password": "short"})
        self.assertIn("admin.password", caught.exception.fields)

    def test_a_masked_secret_means_unchanged(self):
        from wizard import api
        from installer.config.schema import get
        from installer.core.redact import MASK

        api.submit_stage(self.state, "metadata",
                         {"metadata.tmdb_api_key": TEST_TMDB_KEY})
        api.submit_stage(self.state, "metadata",
                         {"metadata.tmdb_api_key": MASK})
        self.assertEqual(get(self.state.config, "metadata.tmdb_api_key"), TEST_TMDB_KEY)

    def test_the_redacted_config_masks_every_secret(self):
        from wizard import api
        api.submit_stage(self.state, "metadata",
                         {"metadata.tmdb_api_key": TEST_TMDB_KEY})
        redacted = api.redacted_config(self.state)
        self.assertNotIn(TEST_TMDB_KEY, json.dumps(redacted))

    def test_state_survives_a_restart(self):
        from wizard import api
        from wizard.session import WizardState

        api.submit_stage(self.state, "welcome",
                         {"meta.instance_name": "Persisted",
                          "deployment.timezone": "UTC",
                          "deployment.accept_licenses": True})
        reopened = WizardState(self.state.path)
        self.assertEqual(reopened.config["meta"]["instance_name"], "Persisted")
        self.assertIn("welcome", reopened.completed)
        self.assertEqual(reopened.current, "admin")

    def test_stages_become_reachable_only_in_order(self):
        from wizard import api
        self.assertTrue(self.state.can_enter("welcome"))
        self.assertFalse(self.state.can_enter("review"))
        for stage in ("welcome",):
            api.submit_stage(self.state, stage,
                             {"deployment.timezone": "UTC",
                              "deployment.accept_licenses": True})
        self.assertTrue(self.state.can_enter("admin"))
        self.assertFalse(self.state.can_enter("modules"))

    def test_the_state_file_is_private(self):
        import stat
        from wizard import api
        api.submit_stage(self.state, "welcome",
                         {"deployment.timezone": "UTC",
                          "deployment.accept_licenses": True})
        mode = stat.S_IMODE(self.state.path.stat().st_mode)
        self.assertEqual(mode & 0o077, 0, f"state file is readable: {mode:04o}")

    def test_a_dangerous_media_path_is_rejected(self):
        from wizard import api
        result = api.check_path({"path": "/etc", "purpose": "movies"})
        self.assertFalse(result["ok"])
        result = api.check_path({"path": "/", "purpose": "movies"})
        self.assertFalse(result["ok"])

    def test_a_privileged_port_is_rejected(self):
        from wizard import api
        self.assertFalse(api.check_port({"port": 80})["ok"])
        self.assertFalse(api.check_port({"port": 0})["ok"])
        self.assertFalse(api.check_port({"port": 70000})["ok"])


class WizardSessions(unittest.TestCase):
    def setUp(self):
        from wizard.session import SessionStore
        self.store = SessionStore("the-correct-bootstrap-token")

    def test_a_wrong_token_is_refused(self):
        self.assertFalse(self.store.check_bootstrap("wrong", "10.0.0.1"))

    def test_the_correct_token_is_accepted(self):
        self.assertTrue(self.store.check_bootstrap("the-correct-bootstrap-token",
                                                   "10.0.0.1"))

    def test_repeated_failures_are_rate_limited(self):
        for _ in range(5):
            self.store.check_bootstrap("wrong", "10.0.0.2")
        # Even the correct token is refused once the address is rate limited.
        self.assertFalse(self.store.check_bootstrap("the-correct-bootstrap-token",
                                                    "10.0.0.2"))

    def test_rate_limiting_is_per_address(self):
        for _ in range(5):
            self.store.check_bootstrap("wrong", "10.0.0.3")
        self.assertTrue(self.store.check_bootstrap("the-correct-bootstrap-token",
                                                   "10.0.0.4"))

    def test_csrf_tokens_are_per_session_and_checked(self):
        a = self.store.create(remote="10.0.0.1")
        b = self.store.create(remote="10.0.0.1")
        self.assertNotEqual(self.store.csrf_token(a), self.store.csrf_token(b))
        self.assertTrue(self.store.check_csrf(a, self.store.csrf_token(a)))
        self.assertFalse(self.store.check_csrf(a, self.store.csrf_token(b)))
        self.assertFalse(self.store.check_csrf(a, ""))

    def test_an_unknown_session_has_no_csrf_token(self):
        self.assertEqual(self.store.csrf_token("not-a-session"), "")
        self.assertIsNone(self.store.get("not-a-session"))


class WizardServerGuards(unittest.TestCase):
    def test_it_refuses_to_bind_a_public_address(self):
        from wizard.server import is_private, main
        self.assertFalse(is_private("8.8.8.8"))
        self.assertTrue(is_private("127.0.0.1"))
        self.assertTrue(is_private("192.168.1.10"))
        code = main(["--host", "8.8.8.8", "--port", "18100", "--token", "x"])
        self.assertEqual(code, 2)

    def test_it_refuses_to_run_without_a_token(self):
        from wizard.server import main
        previous = os.environ.pop("CMV_SETUP_TOKEN", None)
        try:
            code = main(["--host", "127.0.0.1", "--port", "18101"])
        finally:
            if previous is not None:
                os.environ["CMV_SETUP_TOKEN"] = previous
        self.assertEqual(code, 2)

    def test_only_allow_listed_static_files_are_served(self):
        from wizard.server import STATIC_ALLOW
        self.assertEqual(STATIC_ALLOW, {"wizard.css", "wizard.js"})


class CliSurface(unittest.TestCase):
    """The CLI prints to stdout by design; capture it so the test output stays
    readable."""

    def setUp(self):
        import contextlib
        import io
        self._stdout = contextlib.redirect_stdout(io.StringIO())
        self._stdout.__enter__()
        self.addCleanup(lambda: self._stdout.__exit__(None, None, None))

    def test_the_parser_builds(self):
        from installer.cli import build_parser
        parser = build_parser()
        self.assertIsNotNone(parser)

    def test_every_subcommand_has_a_handler(self):
        from installer.cli import COMMANDS, build_parser
        parser = build_parser()
        actions = [a for a in parser._actions if a.dest == "command"]
        for name in actions[0].choices:
            self.assertIn(name, COMMANDS, f"{name} has no handler")

    def test_the_example_configuration_parses_and_defaults_validate(self):
        from installer.cli import _example_config
        from installer.config import loader
        from installer.config.schema import SCHEMA_BY_KEY, get

        text = _example_config()
        parsed = loader.parse_yaml(text)
        # Every documented key must survive the round trip.
        for field in SCHEMA_BY_KEY.values():
            self.assertIsNot(get(parsed, field.key, KeyError), KeyError,
                             f"{field.key} is missing from the example")

    def test_validate_reports_errors_for_an_incomplete_config(self):
        import tempfile
        from installer.cli import main
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as handle:
            handle.write("meta:\n  instance_name: x\n")
            path = handle.name
        try:
            code = main(["--config", path, "--quiet", "--json", "validate"])
        finally:
            os.unlink(path)
        self.assertEqual(code, 2)

    def test_validate_accepts_an_unattended_install_config(self):
        """A file written for unattended install carries the password."""
        from installer.cli import main
        from installer.config import loader

        sandbox = Sandbox()
        self.addCleanup(sandbox.cleanup)
        path = sandbox.dir / "config.yaml"
        loader.save(path, sandbox.config(), mode=0o600)
        code = main(["--config", str(path), "--quiet", "--json", "validate"])
        self.assertEqual(code, 0)

    def test_validate_accepts_an_already_installed_config(self):
        """The file the installer leaves behind has no credentials at all and
        must still be valid - the account lives in the database by then."""
        from installer.config import loader
        from installer.config.schema import errors, validate

        sandbox = Sandbox()
        self.addCleanup(sandbox.cleanup)
        public, _ = loader.split_secrets(hashed_config(sandbox))
        issues = [i for i in errors(validate(public, for_install=False))]
        self.assertEqual(issues, [], [str(i) for i in issues])


if __name__ == "__main__":
    unittest.main()
