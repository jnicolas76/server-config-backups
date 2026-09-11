"""Template rendering: systemd units, Compose files, logrotate and scripts.

A generated unit that systemd rejects, or a Compose file that binds the wrong
address, is the kind of mistake that only shows up on the target machine. These
tests render every template from a real configuration and assert on the
properties that matter for safety and correctness.
"""

from __future__ import annotations

import re
import unittest

from tests.helpers import PACKAGE_ROOT, Sandbox, hashed_config

from installer.templates import (
    TemplateError, list_templates, render, render_template, required_variables,
)


class Renderer(unittest.TestCase):
    def test_substitution(self):
        self.assertEqual(render("a={{ x }}", {"x": 1}), "a=1\n")

    def test_conditionals(self):
        template = "{% if on %}\nyes\n{% else %}\nno\n{% endif %}"
        self.assertEqual(render(template, {"on": True}).strip(), "yes")
        self.assertEqual(render(template, {"on": False}).strip(), "no")

    def test_nested_conditionals(self):
        template = ("{% if a %}\n{% if b %}\nboth\n{% endif %}\nonly-a\n{% endif %}")
        self.assertEqual(render(template, {"a": True, "b": True}).split(),
                         ["both", "only-a"])
        self.assertEqual(render(template, {"a": True, "b": False}).split(), ["only-a"])
        self.assertEqual(render(template, {"a": False, "b": True}).split(), [])

    def test_an_unknown_variable_is_an_error(self):
        with self.assertRaises(TemplateError):
            render("{{ nope }}", {})

    def test_an_unclosed_conditional_is_an_error(self):
        with self.assertRaises(TemplateError):
            render("{% if a %}\nx", {"a": True})

    def test_booleans_render_lowercase(self):
        self.assertEqual(render("v={{ b }}", {"b": True}).strip(), "v=true")

    def test_every_shipped_template_declares_only_known_variables(self):
        """Each template must render from the variables its step supplies."""
        for name in list_templates(PACKAGE_ROOT):
            path = PACKAGE_ROOT / "templates" / name
            variables = required_variables(path.read_text(encoding="utf-8"))
            self.assertIsInstance(variables, set, name)


class SystemdUnits(unittest.TestCase):
    def setUp(self):
        self.sandbox = Sandbox()
        self.addCleanup(self.sandbox.cleanup)
        self.ctx = self.sandbox.context(config=hashed_config(self.sandbox),
                                        dry_run=True)
        from installer.steps.s150_systemd import InstallSystemdUnits
        self.step = InstallSystemdUnits()

    def rendered(self, unit: str) -> str:
        for template_name, unit_name, variables in self.step._units(self.ctx):
            if unit_name == unit:
                return render_template(PACKAGE_ROOT, f"systemd/{template_name}",
                                       variables)
        self.fail(f"{unit} was not generated for this configuration")

    def test_the_main_unit_renders(self):
        unit = self.rendered("cinemediavault.service")
        self.assertIn("[Unit]", unit)
        self.assertIn("[Service]", unit)
        self.assertIn("[Install]", unit)

    def test_the_main_unit_has_no_unsubstituted_placeholders(self):
        for _template, name, variables in self.step._units(self.ctx):
            with self.subTest(unit=name):
                text = render_template(PACKAGE_ROOT, f"systemd/{_template}", variables)
                self.assertNotIn("{{", text)
                self.assertNotIn("{%", text)

    def test_the_service_does_not_run_as_root(self):
        unit = self.rendered("cinemediavault.service")
        user = re.search(r"^User=(.+)$", unit, re.M)
        self.assertIsNotNone(user)
        self.assertNotEqual(user.group(1).strip(), "root")

    def test_hardening_directives_are_present(self):
        unit = self.rendered("cinemediavault.service")
        for directive in ("NoNewPrivileges=true", "ProtectSystem=strict",
                          "ProtectHome=true", "PrivateTmp=true",
                          "RestrictSUIDSGID=true", "CapabilityBoundingSet="):
            self.assertIn(directive, unit, directive)

    def test_writable_paths_are_limited_to_state_log_cache_and_writable_media(self):
        unit = self.rendered("cinemediavault.service")
        writable = []
        for line in unit.splitlines():
            if line.startswith("ReadWritePaths="):
                writable.extend(line.split("=", 1)[1].split())
        self.assertTrue(writable)
        allowed_prefixes = (
            str(self.ctx.layout.state_root), str(self.ctx.layout.log_root),
            str(self.ctx.layout.cache_root),
            self.ctx.get("media.recordings_root"),
            self.ctx.get("media.comic_library_root"),
            self.ctx.get("media.games_root"),
        )
        for path in writable:
            self.assertTrue(any(path.startswith(p) for p in allowed_prefixes if p),
                            f"{path} should not be writable by the service")

    def test_source_libraries_are_read_only(self):
        unit = self.rendered("cinemediavault.service")
        readonly = []
        for line in unit.splitlines():
            if line.startswith("ReadOnlyPaths="):
                readonly.extend(line.split("=", 1)[1].split())
        self.assertIn(self.ctx.get("media.movies_root"), readonly)
        self.assertIn(self.ctx.get("media.tv_root"), readonly)

    def test_restart_is_bounded(self):
        unit = self.rendered("cinemediavault.service")
        self.assertIn("Restart=on-failure", unit)
        self.assertIn("StartLimitBurst=", unit)

    def test_resource_limits_are_set(self):
        unit = self.rendered("cinemediavault.service")
        self.assertIn("MemoryMax=", unit)
        self.assertIn("TasksMax=", unit)

    def test_the_setup_unit_is_never_enabled_at_boot(self):
        """A privileged setup service must not survive a reboot as enabled."""
        text = (PACKAGE_ROOT / "templates" / "systemd" /
                "cinemediavault-setup.service").read_text(encoding="utf-8")
        directives = [line.strip() for line in text.splitlines()
                      if line.strip() and not line.strip().startswith("#")]
        self.assertNotIn("[Install]", directives)
        self.assertFalse([d for d in directives if d.startswith("WantedBy=")],
                         "the setup unit must have no WantedBy directive")

    def test_the_setup_unit_runs_as_root_deliberately(self):
        text = (PACKAGE_ROOT / "templates" / "systemd" /
                "cinemediavault-setup.service").read_text(encoding="utf-8")
        self.assertIn("User=root", text)
        self.assertIn("--host {{ bind_address }}", text)

    def test_timers_are_generated_for_refresh_and_backup(self):
        names = {unit for _t, unit, _v in self.step._units(self.ctx)}
        self.assertIn("cinemediavault-refresh.timer", names)
        self.assertIn("cinemediavault-backup.timer", names)

    def test_every_generated_unit_parses_as_ini(self):
        import configparser
        for template, name, variables in self.step._units(self.ctx):
            with self.subTest(unit=name):
                text = render_template(PACKAGE_ROOT, f"systemd/{template}", variables)
                parser = configparser.ConfigParser(strict=False, allow_no_value=True,
                                                   interpolation=None)
                # systemd permits repeated keys; ConfigParser needs them merged.
                parser.read_string(_merge_duplicate_keys(text))
                self.assertIn("Unit", parser.sections())

    def test_the_whisper_unit_is_resource_limited(self):
        ctx = self.sandbox.context(
            config=hashed_config(self.sandbox,
                                 subtitles={"whisper_enabled": True,
                                            "use_existing": True,
                                            "languages": ["en"]}),
            dry_run=True)
        from installer.steps.s150_systemd import InstallSystemdUnits
        for template, name, variables in InstallSystemdUnits()._units(ctx):
            if name == "cinemediavault-subtitles.service":
                text = render_template(PACKAGE_ROOT, f"systemd/{template}", variables)
                self.assertIn("CPUQuota=", text)
                self.assertIn("MemoryMax=", text)
                self.assertIn("Nice=19", text)
                self.assertNotIn("WantedBy=multi-user.target\nWantedBy", text)
                return
        self.fail("the Whisper unit was not generated")

    def test_the_promo_channel_unit_is_resource_limited_and_off_by_default(self):
        names = {unit for _t, unit, _v in self.step._units(self.ctx)}
        self.assertNotIn("cinemediavault-promo.service", names)
        self.assertNotIn("cinemediavault-promo.timer", names)

        ctx = self.sandbox.context(
            config=hashed_config(self.sandbox,
                                 promo_channel={"enabled": True,
                                                "model_path": "/opt/models/kokoro-v1.0.onnx",
                                                "voices_path": "/opt/models/voices-v1.0.bin"}),
            dry_run=True)
        from installer.steps.s150_systemd import InstallSystemdUnits
        found = False
        for template, name, variables in InstallSystemdUnits()._units(ctx):
            if name == "cinemediavault-promo.service":
                text = render_template(PACKAGE_ROOT, f"systemd/{template}", variables)
                self.assertIn("CPUQuota=", text)
                self.assertIn("MemoryMax=", text)
                self.assertIn("--model /opt/models/kokoro-v1.0.onnx", text)
                self.assertIn("--voices /opt/models/voices-v1.0.bin", text)
                found = True
        self.assertTrue(found, "the promo/barker unit was not generated")


def _merge_duplicate_keys(text: str) -> str:
    """systemd allows a key to repeat; ConfigParser does not."""
    out, seen = [], set()
    section = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped
            seen = set()
            out.append(line)
            continue
        if "=" in stripped and not stripped.startswith("#"):
            key = stripped.split("=", 1)[0]
            marker = (section, key)
            if marker in seen:
                out.append(f"{key}__dup{len(seen)}=" + stripped.split("=", 1)[1])
                seen.add((section, f"{key}__dup{len(seen)}"))
                continue
            seen.add(marker)
        out.append(line)
    return "\n".join(out)


class ComposeFile(unittest.TestCase):
    def setUp(self):
        self.sandbox = Sandbox()
        self.addCleanup(self.sandbox.cleanup)
        self.variables = {
            "bind_address": "192.168.1.10", "port": 3010,
            "timezone": "America/Denver", "days": 14, "max_connections": 1,
            "delay_ms": 2500, "timeout_ms": 45000, "cron_schedule": "20 3 * * *",
            "memory_limit_mb": 3072, "cpu_limit": 1.5, "node_heap_mb": 2048,
        }

    def rendered(self) -> str:
        return render_template(PACKAGE_ROOT, "compose/epg/docker-compose.yml",
                               self.variables)

    def test_it_renders_without_placeholders(self):
        text = self.rendered()
        self.assertNotIn("{{", text)
        self.assertNotIn("{%", text)

    def test_the_collector_binds_a_specific_private_address(self):
        text = self.rendered()
        published = re.search(r'-\s+"([^"]+)"', text).group(1)
        self.assertTrue(published.startswith("192.168.1.10:"),
                        f"published as {published}")
        self.assertNotIn('"0.0.0.0:', text)
        self.assertNotIn('- "3010:3000"', text)     # bare port = all interfaces

    def test_resource_limits_are_applied(self):
        text = self.rendered()
        self.assertIn("mem_limit: 3072m", text)
        self.assertIn("cpus: 1.5", text)

    def test_the_node_heap_is_bounded(self):
        self.assertIn("--max-old-space-size=2048", self.rendered())

    def test_logging_is_bounded(self):
        text = self.rendered()
        self.assertIn("max-size:", text)
        self.assertIn("max-file:", text)

    def test_privilege_escalation_is_blocked(self):
        self.assertIn("no-new-privileges:true", self.rendered())

    def test_a_healthcheck_is_defined(self):
        self.assertIn("healthcheck:", self.rendered())

    def test_the_dockerfile_clones_upstream_rather_than_vendoring_it(self):
        text = render_template(PACKAGE_ROOT, "compose/epg/build/Dockerfile", {})
        self.assertIn("git clone", text)
        self.assertIn("iptv-org/epg", text)


class GeneratedScripts(unittest.TestCase):
    def setUp(self):
        self.sandbox = Sandbox()
        self.addCleanup(self.sandbox.cleanup)
        self.ctx = self.sandbox.context(config=hashed_config(self.sandbox),
                                        dry_run=True)
        from installer.steps.s150_systemd import base_variables
        self.base = base_variables(self.ctx)

    def test_the_health_script_is_valid_shell(self):
        import subprocess
        variables = dict(self.base, health_url="https://127.0.0.1:5000/login",
                         failure_threshold=2, restart_cooldown=300)
        text = render_template(PACKAGE_ROOT, "scripts/cinemediavault-health.sh",
                               variables)
        result = subprocess.run(["bash", "-n"], input=text, text=True,
                                capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_refresh_script_is_valid_shell(self):
        import subprocess
        variables = dict(self.base, movies=True, tv=True)
        text = render_template(PACKAGE_ROOT, "scripts/media-library-refresh.sh",
                               variables)
        result = subprocess.run(["bash", "-n"], input=text, text=True,
                                capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_refresh_script_refuses_an_empty_library(self):
        variables = dict(self.base, movies=True, tv=True)
        text = render_template(PACKAGE_ROOT, "scripts/media-library-refresh.sh",
                               variables)
        self.assertIn("Refusing to rebuild the index from an", text)

    def test_the_health_script_requires_repeated_failures(self):
        variables = dict(self.base, health_url="https://127.0.0.1:5000/login",
                         failure_threshold=2, restart_cooldown=300)
        text = render_template(PACKAGE_ROOT, "scripts/cinemediavault-health.sh",
                               variables)
        self.assertIn("FAILURE_THRESHOLD", text)
        self.assertIn("RESTART_COOLDOWN", text)

    def test_the_creator_wrapper_is_valid_shell(self):
        import subprocess
        from installer.steps.s110_creators import WRAPPER
        text = WRAPPER.format(env_file="/etc/cinemediavault/cinevault.env",
                              creators_dir="/opt/cinemediavault/creators",
                              service_user="cinevault")
        result = subprocess.run(["bash", "-n"], input=text, text=True,
                                capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)


class WizardAssets(unittest.TestCase):
    def test_the_wizard_javascript_is_lexically_sound(self):
        import sys
        sys.path.insert(0, str(PACKAGE_ROOT / "tools"))
        from jscheck import check_file
        problems = check_file(PACKAGE_ROOT / "wizard" / "static" / "wizard.js")
        self.assertEqual(problems, [])

    def test_the_wizard_html_is_balanced(self):
        text = (PACKAGE_ROOT / "wizard" / "templates" / "index.html").read_text(
            encoding="utf-8")
        for tag in ("html", "head", "body", "div", "nav", "main", "header", "footer"):
            opens = len(re.findall(rf"<{tag}[\s>]", text))
            closes = len(re.findall(rf"</{tag}>", text))
            self.assertEqual(opens, closes, f"<{tag}> is unbalanced")

    def test_the_wizard_loads_no_external_resource(self):
        for name in ("templates/index.html", "static/wizard.css", "static/wizard.js"):
            text = (PACKAGE_ROOT / "wizard" / name).read_text(encoding="utf-8")
            for pattern in ("http://", "https://cdn", "//cdn.", "googleapis"):
                if pattern == "http://":
                    # Allowed only inside a comment or a same-origin example.
                    continue
                self.assertNotIn(pattern, text,
                                 f"{name} refers to an external resource")

    def test_the_content_security_policy_forbids_external_sources(self):
        from wizard.server import SECURITY_HEADERS
        csp = SECURITY_HEADERS["Content-Security-Policy"]
        self.assertIn("default-src 'none'", csp)
        self.assertIn("script-src 'self'", csp)
        self.assertIn("frame-ancestors 'none'", csp)
        self.assertNotIn("unsafe-inline", csp)
        self.assertNotIn("unsafe-eval", csp)


if __name__ == "__main__":
    unittest.main()
