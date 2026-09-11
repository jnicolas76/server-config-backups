"""Configuration schema, validation, parsing and round-tripping."""

from __future__ import annotations

import unittest

from tests.helpers import Sandbox, TEST_PASSWORD

from installer.config import loader
from installer.config.schema import (
    SCHEMA, SCHEMA_BY_KEY, SECRET_KEYS, apply_defaults, check_password, errors,
    get, put, validate, warnings,
)


class SchemaIntegrity(unittest.TestCase):
    def test_every_key_is_unique(self):
        keys = [f.key for f in SCHEMA]
        self.assertEqual(len(keys), len(set(keys)))

    def test_every_key_is_documented(self):
        undocumented = [f.key for f in SCHEMA if not f.help and not f.key.startswith("paths.")]
        self.assertEqual(undocumented, [], "every setting needs help text")

    def test_enums_declare_choices(self):
        for field in SCHEMA:
            if field.type == "enum":
                self.assertTrue(field.choices, f"{field.key} is an enum with no choices")

    def test_enum_defaults_are_valid_choices(self):
        for field in SCHEMA:
            if field.type == "enum" and field.default is not None:
                self.assertIn(field.default, field.choices, field.key)

    def test_defaults_pass_their_own_constraints(self):
        for field in SCHEMA:
            if field.default is None or field.required:
                continue
            if field.minimum is not None:
                self.assertGreaterEqual(field.default, field.minimum, field.key)
            if field.maximum is not None:
                self.assertLessEqual(field.default, field.maximum, field.key)

    def test_secret_keys_are_registered(self):
        self.assertIn("admin.password", SECRET_KEYS)
        self.assertIn("metadata.tmdb_api_key", SECRET_KEYS)
        self.assertIn("subtitles.subdl_api_key", SECRET_KEYS)
        self.assertIn("integrations.webex_webhook_url", SECRET_KEYS)


class Validation(unittest.TestCase):
    def setUp(self):
        self.sandbox = Sandbox()
        self.addCleanup(self.sandbox.cleanup)

    def test_a_complete_configuration_is_valid(self):
        issues = errors(validate(self.sandbox.config()))
        self.assertEqual(issues, [], [str(i) for i in issues])

    def test_missing_admin_username_is_an_error(self):
        config = self.sandbox.config()
        put(config, "admin.username", "")
        self.assertTrue(any(i.key == "admin.username" for i in errors(validate(config))))

    def test_duplicate_ports_are_rejected(self):
        config = self.sandbox.config()
        put(config, "modules.comics_port", get(config, "network.https_port"))
        issues = errors(validate(config))
        self.assertTrue(any("already used" in i.message for i in issues))

    def test_public_trusted_network_is_rejected(self):
        config = self.sandbox.config()
        put(config, "network.trusted_networks", ["0.0.0.0/0"])
        self.assertTrue(any(i.key == "network.trusted_networks"
                            for i in errors(validate(config))))

    def test_module_without_a_library_path_is_rejected(self):
        config = self.sandbox.config()
        put(config, "media.music_root", "")
        issues = errors(validate(config))
        self.assertTrue(any(i.key == "media.music_root" for i in issues))

    def test_dvr_requires_livetv(self):
        config = self.sandbox.config()
        put(config, "dvr.enabled", True)
        put(config, "livetv.enabled", False)
        self.assertTrue(any(i.key == "dvr.enabled" for i in errors(validate(config))))

    def test_dvr_requires_a_recording_path(self):
        config = self.sandbox.config()
        put(config, "livetv.enabled", True)
        put(config, "dvr.enabled", True)
        put(config, "media.recordings_root", "")
        self.assertTrue(any(i.key == "media.recordings_root"
                            for i in errors(validate(config))))

    def test_reserving_every_tuner_is_rejected(self):
        config = self.sandbox.config()
        put(config, "livetv.enabled", True)
        put(config, "livetv.discovery", "manual")
        put(config, "livetv.device_address", "192.168.1.50")
        put(config, "livetv.tuner_count", 2)
        put(config, "livetv.reserved_tuners", 2)
        put(config, "dvr.enabled", True)
        self.assertTrue(any(i.key == "livetv.reserved_tuners"
                            for i in errors(validate(config))))

    def test_extended_epg_needs_a_private_bind_address(self):
        config = self.sandbox.config()
        put(config, "livetv.enabled", True)
        put(config, "epg.mode", "extended")
        put(config, "epg.collector_bind_address", "0.0.0.0")
        self.assertTrue(any(i.key == "epg.collector_bind_address"
                            for i in errors(validate(config))))

    def test_nested_media_roots_warn(self):
        config = self.sandbox.config()
        movies = get(config, "media.movies_root")
        put(config, "media.tv_root", movies + "/Inner")
        self.assertTrue(any("nested" in i.message for i in warnings(validate(config))))

    def test_subdl_requires_a_key(self):
        config = self.sandbox.config()
        put(config, "subtitles.subdl_enabled", True)
        put(config, "subtitles.subdl_api_key", "")
        self.assertTrue(any(i.key == "subtitles.subdl_api_key"
                            for i in errors(validate(config))))

    def test_autostarting_the_bulk_queue_requires_installing_it(self):
        config = self.sandbox.config()
        put(config, "transcode.library_queue_autostart", True)
        put(config, "transcode.library_queue_enabled", False)
        self.assertTrue(any(i.key == "transcode.library_queue_autostart"
                            for i in errors(validate(config))))

    def test_unknown_game_module_is_rejected(self):
        config = self.sandbox.config()
        put(config, "modules.games", ["nes", "not-a-console"])
        self.assertTrue(any(i.key == "modules.games" for i in errors(validate(config))))

    def test_promo_channel_requires_operator_supplied_model_files(self):
        config = self.sandbox.config()
        put(config, "promo_channel.enabled", True)
        put(config, "promo_channel.model_path", "")
        put(config, "promo_channel.voices_path", "")
        issue_keys = {i.key for i in errors(validate(config))}
        self.assertIn("promo_channel.model_path", issue_keys)
        self.assertIn("promo_channel.voices_path", issue_keys)

    def test_promo_channel_is_valid_once_model_files_are_set(self):
        config = self.sandbox.config()
        put(config, "promo_channel.enabled", True)
        put(config, "promo_channel.model_path", "/opt/models/kokoro-v1.0.onnx")
        put(config, "promo_channel.voices_path", "/opt/models/voices-v1.0.bin")
        issue_keys = {i.key for i in errors(validate(config))}
        self.assertNotIn("promo_channel.model_path", issue_keys)
        self.assertNotIn("promo_channel.voices_path", issue_keys)

    def test_licence_acceptance_is_required_for_downloads(self):
        config = self.sandbox.config()
        put(config, "deployment.accept_licenses", False)
        self.assertTrue(any(i.key == "deployment.accept_licenses"
                            for i in errors(validate(config))))

    def test_tls_none_without_http_is_rejected(self):
        config = self.sandbox.config()
        put(config, "network.tls_mode", "none")
        put(config, "network.enable_http", False)
        self.assertTrue(any(i.key == "network.tls_mode" for i in errors(validate(config))))

    def test_provided_tls_requires_paths(self):
        config = self.sandbox.config()
        put(config, "network.tls_mode", "provided")
        issues = errors(validate(config))
        self.assertTrue(any(i.key == "network.tls_cert_path" for i in issues))
        self.assertTrue(any(i.key == "network.tls_key_path" for i in issues))

    def test_mount_options_reject_shell_metacharacters(self):
        config = self.sandbox.config()
        put(config, "media.mounts", [{
            "type": "cifs", "source": "//nas/Movies",
            "mountpoint": "/srv/media/movies",
            "options": "vers=3.0;rm -rf /",
        }])
        self.assertTrue(any("fstab option" in i.message for i in errors(validate(config))))


class PasswordPolicy(unittest.TestCase):
    def test_a_good_password_passes(self):
        self.assertEqual(check_password(TEST_PASSWORD), [])

    def test_too_short_is_rejected(self):
        self.assertTrue(check_password("Ab3!xy"))

    def test_common_passwords_are_rejected(self):
        self.assertTrue(check_password("password123"))
        self.assertTrue(check_password("Admin1"))

    def test_too_few_character_classes_is_rejected(self):
        self.assertTrue(check_password("alllowercaseletters"))

    def test_control_characters_are_rejected(self):
        self.assertTrue(check_password("Good-Password-1\n"))

    def test_the_password_is_never_echoed_in_a_message(self):
        secret = "SuperSecret-Password-42!"
        for issue in check_password(secret) + check_password("short"):
            self.assertNotIn(secret, issue.message)


class YamlSubset(unittest.TestCase):
    def test_round_trips(self):
        source = {
            "meta": {"instance_name": "My Vault"},
            "network": {"https_port": 5443, "enable_http": False,
                        "trusted_networks": ["192.168.1.0/24", "10.0.0.0/8"]},
            "media": {"mounts": [
                {"type": "cifs", "source": "//nas/Movies",
                 "mountpoint": "/srv/movies", "manage_fstab": True},
            ]},
            "modules": {"games": ["nes", "sega"]},
        }
        self.assertEqual(loader.parse_yaml(loader.dump_yaml(source)), source)

    def test_comments_and_blank_lines_are_ignored(self):
        text = "# a comment\n\nmeta:\n  # nested comment\n  instance_name: Vault\n"
        self.assertEqual(loader.parse_yaml(text), {"meta": {"instance_name": "Vault"}})

    def test_quoted_values_keep_special_characters(self):
        text = 'meta:\n  instance_name: "Vault: the #1 server"\n'
        self.assertEqual(loader.parse_yaml(text)["meta"]["instance_name"],
                         "Vault: the #1 server")

    def test_booleans_and_numbers_are_typed(self):
        parsed = loader.parse_yaml("a:\n  b: true\n  c: 42\n  d: 1.5\n  e: no\n")
        self.assertIs(parsed["a"]["b"], True)
        self.assertEqual(parsed["a"]["c"], 42)
        self.assertEqual(parsed["a"]["d"], 1.5)
        self.assertIs(parsed["a"]["e"], False)

    def test_tabs_are_rejected(self):
        from installer.core.errors import ConfigError
        with self.assertRaises(ConfigError):
            loader.parse_yaml("meta:\n\tinstance_name: x\n")

    def test_a_value_that_looks_boolean_is_quoted_on_output(self):
        text = loader.dump_yaml({"a": "yes"})
        self.assertEqual(loader.parse_yaml(text)["a"], "yes")


class EnvironmentOverrides(unittest.TestCase):
    def test_a_known_key_is_applied(self):
        config = loader.apply_env_overrides({}, {"CMV_NETWORK_HTTPS_PORT": "5443"})
        self.assertEqual(get(config, "network.https_port"), 5443)

    def test_a_list_is_split(self):
        config = loader.apply_env_overrides({}, {"CMV_MODULES_GAMES": "nes,sega"})
        self.assertEqual(get(config, "modules.games"), ["nes", "sega"])

    def test_an_unknown_key_is_an_error(self):
        from installer.core.errors import ConfigError
        with self.assertRaises(ConfigError):
            loader.apply_env_overrides({}, {"CMV_NOT_A_SETTING": "x"})

    def test_a_bad_value_is_an_error(self):
        from installer.core.errors import ConfigError
        with self.assertRaises(ConfigError):
            loader.apply_env_overrides({}, {"CMV_NETWORK_HTTPS_PORT": "not-a-number"})


class SecretSplitting(unittest.TestCase):
    def setUp(self):
        self.sandbox = Sandbox()
        self.addCleanup(self.sandbox.cleanup)

    def test_secrets_are_removed_from_the_public_document(self):
        public, secrets = loader.split_secrets(self.sandbox.config())
        for key in SECRET_KEYS:
            self.assertIn(get(public, key), ("", None), key)
        self.assertIn("metadata.tmdb_api_key", secrets)

    def test_the_public_document_contains_no_secret_value(self):
        config = self.sandbox.config()
        public, _ = loader.split_secrets(config)
        rendered = loader.dump_yaml(public)
        self.assertNotIn(TEST_PASSWORD, rendered)
        self.assertNotIn(get(config, "metadata.tmdb_api_key"), rendered)

    def test_the_redacted_example_masks_every_secret(self):
        rendered = loader.redacted_example(self.sandbox.config())
        self.assertNotIn(TEST_PASSWORD, rendered)
        self.assertIn("***REDACTED***", rendered)


if __name__ == "__main__":
    unittest.main()
