"""The documentation must describe the code that actually ships.

Generated documents are regenerated here and compared; hand-written ones are
checked for the promises they make about behaviour, so a document cannot quietly
become wrong.
"""

from __future__ import annotations

import re
import subprocess
import sys
import unittest
from pathlib import Path

from tests.helpers import PACKAGE_ROOT


class GeneratedDocumentsAreCurrent(unittest.TestCase):
    def _regenerate(self, script: str, target: str) -> tuple[str, str]:
        path = PACKAGE_ROOT / target
        before = path.read_text(encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(PACKAGE_ROOT / "tools" / script)],
            capture_output=True, text=True, cwd=str(PACKAGE_ROOT))
        self.assertEqual(result.returncode, 0, result.stderr)
        after = path.read_text(encoding="utf-8")
        return before, after

    def test_the_configuration_reference_matches_the_schema(self):
        before, after = self._regenerate("gen-config-reference.py",
                                         "docs/CONFIGURATION-REFERENCE.md")
        self.assertEqual(before, after,
                         "docs/CONFIGURATION-REFERENCE.md is stale. Run "
                         "tools/gen-config-reference.py.")

    def test_the_json_schema_matches_the_schema(self):
        before, after = self._regenerate("gen-json-schema.py",
                                         "config/cinemediavault.schema.json")
        self.assertEqual(before, after,
                         "config/cinemediavault.schema.json is stale. Run "
                         "tools/gen-json-schema.py.")

    def test_every_setting_appears_in_the_reference(self):
        from installer.config.schema import SCHEMA
        text = (PACKAGE_ROOT / "docs" / "CONFIGURATION-REFERENCE.md").read_text(
            encoding="utf-8")
        for field in SCHEMA:
            self.assertIn(f"`{field.key.split('.', 1)[1]}`", text, field.key)


class ExampleConfigurationsAreValid(unittest.TestCase):
    def test_every_shipped_example_validates(self):
        from installer.config import loader
        from installer.config.schema import errors, validate

        examples = sorted((PACKAGE_ROOT / "docs" / "examples").glob("*.yaml"))
        self.assertTrue(examples, "no example configurations were found")
        for path in examples:
            with self.subTest(example=path.name):
                config = loader.load(path)
                issues = errors(validate(config))
                self.assertEqual(issues, [],
                                 f"{path.name}: {[str(i) for i in issues]}")

    def test_the_generated_example_round_trips(self):
        from installer.cli import _example_config
        from installer.config import loader
        parsed = loader.parse_yaml(_example_config())
        self.assertIn("meta", parsed)
        self.assertIn("network", parsed)

    def test_no_example_contains_a_real_looking_secret(self):
        for path in sorted((PACKAGE_ROOT / "docs" / "examples").glob("*.yaml")):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("pbkdf2_sha256$", text)
            # Any password present must obviously be a placeholder.
            for line in text.splitlines():
                if re.match(r"\s*password:\s*\S", line) and "CHANGE-ME" not in line:
                    self.fail(f"{path.name} has a password that is not a placeholder")


class DocumentationLinks(unittest.TestCase):
    def test_every_local_link_in_the_readme_resolves(self):
        readme = PACKAGE_ROOT / "README.md"
        text = readme.read_text(encoding="utf-8")
        missing = []
        for target in re.findall(r"\]\((?!https?://)([^)#]+)", text):
            if not (PACKAGE_ROOT / target).exists():
                missing.append(target)
        self.assertEqual(missing, [], f"broken links in README.md: {missing}")

    def test_every_local_link_in_the_docs_resolves(self):
        missing = []
        for doc in sorted((PACKAGE_ROOT / "docs").glob("*.md")):
            text = doc.read_text(encoding="utf-8")
            for target in re.findall(r"\]\((?!https?://)([^)#]+)", text):
                resolved = (doc.parent / target).resolve()
                if not resolved.exists():
                    missing.append(f"{doc.name} -> {target}")
        self.assertEqual(missing, [], f"broken links: {missing}")

    def test_the_readme_documents_every_shipped_document(self):
        readme = (PACKAGE_ROOT / "README.md").read_text(encoding="utf-8")
        for doc in sorted((PACKAGE_ROOT / "docs").glob("*.md")):
            self.assertIn(doc.name, readme, f"{doc.name} is not referenced")


class PromisesTheDocumentationMakes(unittest.TestCase):
    """Claims a reader will rely on, checked against the code."""

    def test_the_stated_pbkdf2_iterations_are_what_is_used(self):
        from installer.config.secrets import PBKDF2_ROUNDS
        readme = (PACKAGE_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn(f"{PBKDF2_ROUNDS:,}", readme,
                      "the README states an iteration count that does not match "
                      "installer/config/secrets.py")

    def test_the_stated_minimum_password_length_matches(self):
        from installer.config.schema import MIN_PASSWORD_LENGTH
        readme = (PACKAGE_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn(f"at least {MIN_PASSWORD_LENGTH} characters", readme)

    def test_the_stated_health_threshold_matches_the_default(self):
        from installer.config.schema import SCHEMA_BY_KEY
        threshold = SCHEMA_BY_KEY["ops.health_failure_threshold"].default
        readme = (PACKAGE_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertEqual(threshold, 2)
        self.assertIn("**two**", readme)

    def test_the_supported_releases_match_the_code(self):
        from installer.version import SUPPORTED_UBUNTU
        readme = (PACKAGE_ROOT / "README.md").read_text(encoding="utf-8")
        for release in SUPPORTED_UBUNTU:
            self.assertIn(release, readme)

    def test_the_default_ports_in_the_readme_match_the_schema(self):
        from installer.config.schema import SCHEMA_BY_KEY
        readme = (PACKAGE_ROOT / "README.md").read_text(encoding="utf-8")
        for key in ("network.https_port", "modules.comics_port",
                    "modules.bookvault_port", "epg.collector_port"):
            self.assertIn(str(SCHEMA_BY_KEY[key].default), readme, key)

    def test_the_third_party_notices_cover_every_downloaded_runtime(self):
        from installer.steps.s100_modules import RUNTIME_SOURCES
        notices = (PACKAGE_ROOT / "docs" / "THIRD-PARTY-NOTICES.md").read_text(
            encoding="utf-8").lower()
        for name, meta in RUNTIME_SOURCES.items():
            self.assertIn(name.lower(), notices, f"{name} is not in the notices")
            self.assertIn(meta["licence"].lower(), notices,
                          f"{name}'s licence is not stated")

    def test_the_third_party_notices_cover_every_apt_package(self):
        from installer.preflight.deps import BASE_PACKAGES, FEATURE_PACKAGES, MEDIA_PACKAGES
        notices = (PACKAGE_ROOT / "docs" / "THIRD-PARTY-NOTICES.md").read_text(
            encoding="utf-8")
        packages = set(BASE_PACKAGES) | set(MEDIA_PACKAGES)
        for group in FEATURE_PACKAGES.values():
            packages.update(group)
        for package in sorted(packages):
            self.assertIn(package, notices, f"{package} is not in the notices")

    def test_the_bundled_asset_has_a_licence_notice(self):
        assets = PACKAGE_ROOT / "payload" / "assets"
        for asset in assets.glob("*.js"):
            notice = assets / f"{asset.name}.LICENSE.txt"
            self.assertTrue(notice.is_file(),
                            f"{asset.name} is bundled without a licence notice")

    def test_the_acceptance_checklist_covers_the_critical_safety_checks(self):
        text = (PACKAGE_ROOT / "docs" /
                "CLEAN-VM-ACCEPTANCE-CHECKLIST.md").read_text(encoding="utf-8").lower()
        for promise in ("media is identical", "idempotenc", "no secret",
                        "resumab", "rollback", "uninstall"):
            self.assertIn(promise, text, f"the checklist does not cover: {promise}")


if __name__ == "__main__":
    unittest.main()
