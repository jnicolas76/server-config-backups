"""Secret redaction, path safety, password hashing and command safety.

These are the tests that protect the properties an operator cannot check for
themselves: that a credential never reaches disk, that a media path cannot be
turned into a system path, and that no configuration value is ever interpreted
as shell syntax.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from tests.helpers import Sandbox, TEST_PASSWORD, TEST_TMDB_KEY, TEST_WEBEX

from installer.config.schema import (
    FORBIDDEN_MEDIA_PATHS, PathSafetyError, check_media_path, check_system_path,
    is_private_address, is_private_cidr,
)
from installer.config.secrets import (
    ENV_NAME_RE, SecretStore, env_quote, hash_password, render_env_file,
    verify_password,
)
from installer.core.redact import MASK, Redactor


class Redaction(unittest.TestCase):
    def setUp(self):
        self.redactor = Redactor()

    def test_a_registered_value_is_masked(self):
        self.redactor.register("super-secret-value")
        self.assertNotIn("super-secret-value",
                         self.redactor.redact("token=super-secret-value here"))

    def test_short_values_are_not_registered(self):
        self.redactor.register("abc")
        self.assertEqual(self.redactor.registered_count, 0)

    def test_api_key_assignments_are_masked_without_registration(self):
        for text in (
            "api_key=abcdef1234567890",
            "API_KEY: abcdef1234567890",
            'password="hunter2hunter2"',
            "TMDB_READ_ACCESS_TOKEN=eyJhbGciOiJIUzI1NiJ9.eyJhdWQiOiJ4In0.sig",
            "--password mysecretvalue",
            "client_secret: 'abcdef1234567890'",
        ):
            with self.subTest(text=text):
                self.assertIn(MASK, self.redactor.redact(text))

    def test_authorization_headers_are_masked(self):
        redacted = self.redactor.redact("Authorization: Bearer abcdef123456")
        self.assertNotIn("abcdef123456", redacted)

    def test_credentials_in_a_url_are_masked(self):
        redacted = self.redactor.redact("https://user:hunter2pass@nas.local/share")
        self.assertNotIn("hunter2pass", redacted)
        self.assertIn("user", redacted)

    def test_webhook_paths_are_masked(self):
        redacted = self.redactor.redact(TEST_WEBEX)
        self.assertNotIn("TESTTOKENVALUE123456", redacted)

    def test_private_keys_are_masked(self):
        pem = ("-----BEGIN RSA PRIVATE KEY-----\n"
               "MIIEowIBAAKCAQEA1234567890\n"
               "-----END RSA PRIVATE KEY-----")
        self.assertNotIn("MIIEowIBAAKCAQEA", self.redactor.redact(pem))

    def test_argv_masking_masks_the_value_after_a_password_flag(self):
        masked = self.redactor.redact_argv(["mysql", "--password", "hunter2", "db"])
        self.assertNotIn("hunter2", " ".join(masked))
        self.assertIn("db", masked)

    def test_mapping_masking_masks_by_key_name(self):
        masked = self.redactor.redact_mapping(
            {"tmdb_api_key": "abc123def456", "hostname": "vault.local",
             "nested": {"webex_webhook_url": "https://x/hooks/abcdefghijkl"}})
        self.assertEqual(masked["tmdb_api_key"], MASK)
        self.assertEqual(masked["hostname"], "vault.local")
        self.assertEqual(masked["nested"]["webex_webhook_url"], MASK)

    def test_registering_a_config_masks_every_declared_secret(self):
        sandbox = Sandbox()
        self.addCleanup(sandbox.cleanup)
        self.redactor.register_config(sandbox.config())
        text = f"password={TEST_PASSWORD} key={TEST_TMDB_KEY} hook={TEST_WEBEX}"
        redacted = self.redactor.redact(text)
        self.assertNotIn(TEST_PASSWORD, redacted)
        self.assertNotIn(TEST_TMDB_KEY, redacted)
        self.assertNotIn("TESTTOKENVALUE123456", redacted)

    def test_a_longer_secret_containing_a_shorter_one_is_fully_masked(self):
        self.redactor.register("secretvalue")
        self.redactor.register("secretvalue-extended")
        self.assertNotIn("secretvalue", self.redactor.redact("x secretvalue-extended y"))


class LoggerRedaction(unittest.TestCase):
    def test_the_logger_never_writes_a_registered_secret(self):
        import json
        import tempfile
        from installer.core.logging import InstallLogger
        from installer.core.redact import REDACTOR

        REDACTOR.register("logger-test-secret-value")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "log.jsonl"
            logger = InstallLogger(path, console=False)
            logger.info("connecting with logger-test-secret-value now")
            logger.error("failed: token=logger-test-secret-value")
            logger.close()
            content = path.read_text(encoding="utf-8")
        self.assertNotIn("logger-test-secret-value", content)
        self.assertIn(MASK, content)
        for line in content.splitlines():
            json.loads(line)          # every line must stay valid JSON

    def test_the_log_file_is_not_world_readable(self):
        import os
        import stat
        import tempfile
        from installer.core.logging import InstallLogger

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "log.jsonl"
            logger = InstallLogger(path, console=False)
            logger.info("x")
            logger.close()
            mode = stat.S_IMODE(os.stat(path).st_mode)
        self.assertEqual(mode & 0o007, 0, f"log is world-accessible: {mode:04o}")


class MediaPathSafety(unittest.TestCase):
    def test_system_directories_are_refused(self):
        for path in sorted(FORBIDDEN_MEDIA_PATHS):
            with self.subTest(path=path):
                with self.assertRaises(PathSafetyError):
                    check_media_path(path, label="test")

    def test_shallow_paths_are_refused(self):
        for path in ("/", "/data", "/x"):
            with self.subTest(path=path):
                with self.assertRaises(PathSafetyError):
                    check_media_path(path, label="test")

    def test_traversal_is_refused(self):
        for path in ("/srv/media/../../etc", "/srv/../etc/passwd"):
            with self.subTest(path=path):
                with self.assertRaises(PathSafetyError):
                    check_media_path(path, label="test")

    def test_relative_paths_are_refused(self):
        with self.assertRaises(PathSafetyError):
            check_media_path("media/movies", label="test")

    def test_null_bytes_are_refused(self):
        with self.assertRaises(PathSafetyError):
            check_media_path("/srv/media\x00/movies", label="test")

    def test_a_real_library_path_is_accepted(self):
        for path in ("/srv/cinemediavault/Movies", "/mnt/nas/Movies",
                     "/media/library/TV Shows", "/home/user/Media/Music"):
            with self.subTest(path=path):
                self.assertTrue(check_media_path(path, label="test").startswith("/"))

    def test_trailing_slashes_are_normalised(self):
        self.assertEqual(check_media_path("/srv/media/Movies/", label="t"),
                         "/srv/media/Movies")

    def test_system_paths_may_be_shallow_but_not_root(self):
        self.assertEqual(check_system_path("/opt/cinemediavault", label="t"),
                         "/opt/cinemediavault")
        with self.assertRaises(PathSafetyError):
            check_system_path("/", label="t")


class NetworkSafety(unittest.TestCase):
    def test_the_whole_internet_is_not_private(self):
        # Python's ipaddress calls 0.0.0.0/0 private because 0.0.0.0/8 is
        # reserved. Accepting it would open the firewall to the world.
        self.assertFalse(is_private_cidr("0.0.0.0/0"))
        self.assertFalse(is_private_cidr("::/0"))

    def test_wildcard_addresses_are_not_private(self):
        self.assertFalse(is_private_address("0.0.0.0"))
        self.assertFalse(is_private_address("::"))

    def test_real_private_ranges_are_accepted(self):
        for cidr in ("192.168.1.0/24", "10.0.0.0/8", "172.16.0.0/12",
                     "127.0.0.0/8", "fc00::/7"):
            with self.subTest(cidr=cidr):
                self.assertTrue(is_private_cidr(cidr))

    def test_public_ranges_are_rejected(self):
        for cidr in ("8.8.8.0/24", "1.0.0.0/8", "203.0.113.0/24"):
            with self.subTest(cidr=cidr):
                self.assertFalse(is_private_cidr(cidr))

    def test_malformed_input_is_rejected(self):
        self.assertFalse(is_private_cidr("not-a-network"))
        self.assertFalse(is_private_address("not-an-address"))


class PasswordHashing(unittest.TestCase):
    def test_the_hash_format_matches_the_application(self):
        digest = hash_password(TEST_PASSWORD)
        scheme, rounds, salt, value = digest.split("$", 3)
        self.assertEqual(scheme, "pbkdf2_sha256")
        self.assertEqual(int(rounds), 260000)
        self.assertTrue(salt and value)

    def test_verification_round_trips(self):
        digest = hash_password(TEST_PASSWORD)
        self.assertTrue(verify_password(TEST_PASSWORD, digest))
        self.assertFalse(verify_password(TEST_PASSWORD + "x", digest))

    def test_each_hash_uses_a_fresh_salt(self):
        self.assertNotEqual(hash_password(TEST_PASSWORD), hash_password(TEST_PASSWORD))

    def test_the_hash_does_not_contain_the_password(self):
        self.assertNotIn(TEST_PASSWORD, hash_password(TEST_PASSWORD))

    def test_an_empty_password_is_refused(self):
        with self.assertRaises(ValueError):
            hash_password("")

    def test_a_corrupt_hash_verifies_false_rather_than_raising(self):
        for bad in ("", "not-a-hash", "pbkdf2_sha256$abc$def$ghi", "md5$1$x$y"):
            with self.subTest(bad=bad):
                self.assertFalse(verify_password("x", bad))


class EnvironmentFileWriting(unittest.TestCase):
    def test_values_are_quoted_and_escaped(self):
        rendered = render_env_file({"A": 'has "quotes" and $dollars'})
        self.assertIn('A="has \\"quotes\\" and $dollars"', rendered)

    def test_newlines_cannot_inject_another_variable(self):
        rendered = render_env_file({"A": "value\nINJECTED=bad"})
        self.assertEqual(len([l for l in rendered.splitlines()
                              if l and not l.startswith("#")]), 1)
        self.assertIn("\\n", rendered)

    def test_invalid_variable_names_are_refused(self):
        for name in ("lower", "1START", "HAS-DASH", "HAS SPACE", ""):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    render_env_file({name: "x"})

    def test_the_secret_file_is_not_world_readable(self):
        import os
        import stat
        import tempfile
        sandbox = Sandbox()
        self.addCleanup(sandbox.cleanup)
        path = sandbox.dir / "secrets.env"
        SecretStore(path).write(sandbox.config())
        mode = stat.S_IMODE(os.stat(path).st_mode)
        self.assertEqual(mode & 0o007, 0, f"secrets are world-accessible: {mode:04o}")

    def test_the_secret_file_round_trips(self):
        sandbox = Sandbox()
        self.addCleanup(sandbox.cleanup)
        path = sandbox.dir / "secrets.env"
        store = SecretStore(path)
        store.write(sandbox.config())
        values = store.read()
        self.assertEqual(values["TMDB_API_KEY"], TEST_TMDB_KEY)
        self.assertEqual(values["CINEVAULT_WEBEX_WEBHOOK_URL"], TEST_WEBEX)

    def test_the_admin_password_is_never_in_the_secret_file(self):
        sandbox = Sandbox()
        self.addCleanup(sandbox.cleanup)
        path = sandbox.dir / "secrets.env"
        SecretStore(path).write(sandbox.config())
        self.assertNotIn(TEST_PASSWORD, path.read_text(encoding="utf-8"))


class AdminBootstrapFile(unittest.TestCase):
    def test_it_refuses_a_plaintext_password(self):
        from installer.config.secrets import AdminBootstrap
        sandbox = Sandbox()
        self.addCleanup(sandbox.cleanup)
        bootstrap = AdminBootstrap(sandbox.dir / "admin.json")
        with self.assertRaises(ValueError):
            bootstrap.write(username="x", password_hash=TEST_PASSWORD)

    def test_it_writes_only_a_hash(self):
        import json
        from installer.config.secrets import AdminBootstrap
        sandbox = Sandbox()
        self.addCleanup(sandbox.cleanup)
        path = sandbox.dir / "admin.json"
        AdminBootstrap(path).write(username="tester",
                                   password_hash=hash_password(TEST_PASSWORD))
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertTrue(payload["password_hash"].startswith("pbkdf2_sha256$"))
        self.assertNotIn(TEST_PASSWORD, path.read_text(encoding="utf-8"))


class ShippedPayloadSafety(unittest.TestCase):
    """The package itself must contain no usable credential.

    The installer patches the default administrator out when it installs, and
    refuses to proceed if it cannot. The payload is additionally patched at
    build time so that a copy of the application taken straight out of this
    package cannot create a super-administrator with a publicly known password
    either.
    """

    def setUp(self):
        self.root = Path(__file__).resolve().parents[1]
        self.app = self.root / "payload" / "app" / "cinemediavault.py"

    def test_the_shipped_application_has_no_default_administrator(self):
        text = self.app.read_text(encoding="utf-8")
        self.assertNotIn('password_hash("admin1")', text)
        self.assertNotIn("VALUES('jnicolas'", text)
        self.assertIn("CineMediaVault installer patch", text)

    def test_the_shipped_application_compiles(self):
        import py_compile
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            py_compile.compile(str(self.app), cfile=str(Path(tmp) / "o.pyc"),
                               doraise=True)

    def test_no_file_in_the_package_contains_a_password_hash(self):
        import re
        pattern = re.compile(r"pbkdf2_sha256\$\d+\$[A-Za-z0-9+/=]{16,}\$")
        offenders = []
        for path in self.root.rglob("*"):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if pattern.search(text):
                offenders.append(str(path.relative_to(self.root)))
        self.assertEqual(offenders, [])

    def test_no_file_in_the_package_contains_a_private_key(self):
        import re
        # A marker alone is a pattern definition; a marker followed by base64
        # body lines is an actual key.
        pattern = re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----\s*\n[A-Za-z0-9+/=]{40,}")
        offenders = []
        for path in self.root.rglob("*"):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if pattern.search(text):
                offenders.append(str(path.relative_to(self.root)))
        self.assertEqual(offenders, [])

    def test_no_shipped_file_contains_a_live_host_address(self):
        """The original deployment's addresses must not survive as defaults."""
        import re
        pattern = re.compile(r"192\.168\.1\.(?:19|20|121|134|213|232|240)\b")
        offenders = []
        for area in ("installer", "wizard", "templates", "config"):
            for path in (self.root / area).rglob("*"):
                if not path.is_file() or "__pycache__" in path.parts:
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
                for number, line in enumerate(text.splitlines(), 1):
                    if pattern.search(line) and not line.strip().startswith("#"):
                        offenders.append(
                            f"{path.relative_to(self.root)}:{number}")
        self.assertEqual(offenders, [])


class CommandSafety(unittest.TestCase):
    def test_no_call_anywhere_passes_shell_true(self):
        """Parse the tree rather than grep it: prose mentioning shell=True in a
        docstring is fine, an actual keyword argument is not."""
        import ast

        root = Path(__file__).resolve().parents[1]
        offenders = []
        for path in list((root / "installer").rglob("*.py")) + \
                list((root / "wizard").rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    for keyword in node.keywords:
                        if keyword.arg == "shell" and \
                                isinstance(keyword.value, ast.Constant) and \
                                keyword.value.value is True:
                            offenders.append(f"{path.relative_to(root)}:{node.lineno}")
        self.assertEqual(offenders, [])

    def test_no_module_calls_os_system_or_popen(self):
        import ast

        root = Path(__file__).resolve().parents[1]
        offenders = []
        banned = {("os", "system"), ("os", "popen"), ("subprocess", "getoutput"),
                  ("subprocess", "getstatusoutput")}
        for path in list((root / "installer").rglob("*.py")) + \
                list((root / "wizard").rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                        and isinstance(node.func.value, ast.Name):
                    if (node.func.value.id, node.func.attr) in banned:
                        offenders.append(f"{path.relative_to(root)}:{node.lineno}")
        self.assertEqual(offenders, [])

    def test_a_path_with_shell_metacharacters_stays_one_argument(self):
        from installer.core.runner import Runner
        runner = Runner(dry_run=True)
        nasty = "/srv/media/My Movies; rm -rf /"
        result = runner.run(["echo", nasty], allow_in_dry_run=True)
        self.assertEqual(result.stdout.strip(), nasty)


class FstabSafety(unittest.TestCase):
    def test_unsafe_mount_options_are_refused(self):
        from installer.core.errors import InstallerError
        from installer.discovery.mounts import build_options
        with self.assertRaises(InstallerError):
            build_options("cifs", "vers=3.0 0 0\n/etc/shadow /tmp/x none bind")

    def test_safe_defaults_are_always_present(self):
        options = build_options_helper()
        for required in ("_netdev", "nofail", "x-systemd.automount"):
            self.assertIn(required, options)

    def test_spaces_in_paths_are_escaped(self):
        from installer.discovery.mounts import build_entry
        entry = build_entry({"type": "cifs", "source": "//nas/My Share",
                             "mountpoint": "/srv/media/My Movies"})
        self.assertIn("\\040", entry)
        self.assertEqual(len(entry.split("\t")), 7)

    def test_managed_entries_carry_the_tag(self):
        from installer.discovery.mounts import TAG, build_entry
        entry = build_entry({"type": "nfs", "source": "nas:/export",
                             "mountpoint": "/srv/media/movies"})
        self.assertIn(TAG, entry)


def build_options_helper() -> str:
    from installer.discovery.mounts import build_options
    return build_options("cifs", "uid=1000")


if __name__ == "__main__":
    unittest.main()
