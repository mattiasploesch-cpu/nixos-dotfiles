"""Tests fuer Konfigurationsparser, Standarddatei und Validierung."""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from weston_session.config import ConfigError, Settings, load_config, parse_config
from weston_session.default_config import DEFAULT_CONFIG

MINIMAL = """
[settings]
default_compositor = "weston"

[compositors.weston]
command = ["weston", "--backend=x11", "--socket=wayland-weston"]
socket = "wayland-weston"
"""


class DefaultConfigTest(unittest.TestCase):
    def test_default_config_is_valid(self) -> None:
        config = parse_config(DEFAULT_CONFIG)
        self.assertIn("weston", config.compositors)
        self.assertIn("niri", config.programs)
        self.assertIn("dms", config.programs)
        self.assertEqual(config.settings.startup_timeout, 15.0)
        self.assertEqual(config.settings.default_compositor, "weston")

    def test_weston_uses_x11_backend_and_survives_exit(self) -> None:
        weston = parse_config(DEFAULT_CONFIG).compositors["weston"]
        self.assertEqual(weston.backend, "x11")
        self.assertIn("--backend=x11", weston.command)
        self.assertEqual(weston.socket, "wayland-weston")
        self.assertFalse(weston.should_stop_on_exit(Settings()))

    def test_dependencies_are_configured(self) -> None:
        config = parse_config(DEFAULT_CONFIG)
        self.assertEqual(config.programs["niri"].requires, ("weston",))
        self.assertEqual(config.programs["dms"].requires, ("niri",))
        self.assertEqual(config.programs["wayland-info"].ready_check, "exit")

    def test_niri_ready_pattern_matches_log_line(self) -> None:
        pattern = re.compile(parse_config(DEFAULT_CONFIG).programs["niri"].ready_pattern)
        match = pattern.search("INFO niri: listening on Wayland socket: wayland-3")
        self.assertIsNotNone(match)
        self.assertEqual(match.group("socket"), "wayland-3")


class ConfigFileTest(unittest.TestCase):
    def test_missing_config_is_created(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "config.toml"
            config = load_config(path)
            self.assertTrue(path.exists())
            self.assertIn("weston", config.compositors)
            self.assertIn("[settings]", path.read_text(encoding="utf-8"))

    def test_missing_config_without_create_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ConfigError) as context:
                load_config(Path(tmp) / "fehlt.toml", create=False)
            self.assertIn("fehlt", str(context.exception))

    def test_invalid_toml_reports_line(self) -> None:
        with self.assertRaises(ConfigError) as context:
            parse_config("[settings]\nstartup_timeout = \n", "cfg.toml")
        message = str(context.exception)
        self.assertIn("TOML-Syntax", message)
        self.assertIn("line 2", message)


class ValidationTest(unittest.TestCase):
    def errors(self, text: str) -> list[str]:
        with self.assertRaises(ConfigError) as context:
            parse_config(text, "cfg.toml")
        return context.exception.errors

    def test_command_must_be_argument_list(self) -> None:
        errors = self.errors(
            """
[compositors.weston]
command = "weston --backend=x11"
"""
        )
        self.assertTrue(any("Liste" in error for error in errors), errors)

    def test_empty_command_is_rejected(self) -> None:
        errors = self.errors("[compositors.weston]\ncommand = []\n")
        self.assertTrue(any("command" in error for error in errors), errors)

    def test_unknown_requirement_is_rejected(self) -> None:
        errors = self.errors(
            MINIMAL
            + """
[programs.dms]
command = ["dms", "run"]
requires = ["niri"]
"""
        )
        self.assertTrue(any("niri" in error and "definiert" in error for error in errors), errors)

    def test_cycle_is_rejected_with_chain(self) -> None:
        errors = self.errors(
            """
[compositors.a]
command = ["a"]
requires = ["b"]

[compositors.b]
command = ["b"]
requires = ["a"]
"""
        )
        joined = " ".join(errors)
        self.assertIn("Zyklische Abhaengigkeit", joined)
        self.assertIn("a -> b -> a", joined)

    def test_self_dependency_is_rejected(self) -> None:
        errors = self.errors("[compositors.a]\ncommand = [\"a\"]\nrequires = [\"a\"]\n")
        self.assertTrue(any("Zyklisch" in error for error in errors), errors)

    def test_unknown_ready_check_is_rejected(self) -> None:
        errors = self.errors(
            MINIMAL + '\n[programs.x]\ncommand = ["x"]\nready_check = "magic"\n'
        )
        self.assertTrue(any("ready_check" in error for error in errors), errors)

    def test_command_check_requires_ready_command(self) -> None:
        errors = self.errors(
            MINIMAL + '\n[programs.x]\ncommand = ["x"]\nready_check = "command"\n'
        )
        self.assertTrue(any("ready_command" in error for error in errors), errors)

    def test_numeric_settings_are_validated(self) -> None:
        errors = self.errors(
            MINIMAL.replace(
                'default_compositor = "weston"',
                'default_compositor = "weston"\nstartup_timeout = "15"',
            )
        )
        self.assertTrue(any("startup_timeout" in error for error in errors), errors)

    def test_unknown_default_compositor_is_rejected(self) -> None:
        errors = self.errors(
            MINIMAL.replace('default_compositor = "weston"', 'default_compositor = "gibtsnicht"')
        )
        self.assertTrue(any("default_compositor" in error for error in errors), errors)

    def test_duplicate_name_between_kinds_is_rejected(self) -> None:
        errors = self.errors(
            MINIMAL + '\n[programs.weston]\ncommand = ["weston"]\n'
        )
        self.assertTrue(any("bereits" in error for error in errors), errors)

    def test_invalid_socket_detection_is_rejected(self) -> None:
        errors = self.errors(
            MINIMAL.replace('socket = "wayland-weston"', 'socket_detection = "zauber"', 1)
        )
        self.assertTrue(any("socket_detection" in error for error in errors), errors)

    def test_env_values_must_be_strings(self) -> None:
        errors = self.errors(
            MINIMAL + '\n[programs.x]\ncommand = ["x"]\nenv = { A = 1 }\n'
        )
        self.assertTrue(any("env.A" in error for error in errors), errors)

    def test_requires_must_be_list(self) -> None:
        errors = self.errors(
            MINIMAL + '\n[programs.x]\ncommand = ["x"]\nrequires = "weston"\n'
        )
        self.assertTrue(any("requires" in error for error in errors), errors)

    def test_ready_pattern_must_compile(self) -> None:
        errors = self.errors(
            MINIMAL + '\n[programs.x]\ncommand = ["x"]\nready_pattern = "(("\n'
        )
        self.assertTrue(any("ready_pattern" in error for error in errors), errors)

    def test_unknown_settings_key_only_warns(self) -> None:
        config = parse_config(
            MINIMAL.replace(
                'default_compositor = "weston"',
                'default_compositor = "weston"\nunbekannt = 1',
            )
        )
        self.assertTrue(any("unbekannt" in warning for warning in config.warnings))


class EntrySemanticsTest(unittest.TestCase):
    def test_timeout_falls_back_to_settings(self) -> None:
        config = parse_config(
            MINIMAL.replace(
                'default_compositor = "weston"',
                'default_compositor = "weston"\nstartup_timeout = 42',
            )
        )
        self.assertEqual(config.compositors["weston"].timeout(config.settings), 42.0)

    def test_per_entry_timeout_wins(self) -> None:
        config = parse_config(
            MINIMAL + '\n[programs.x]\ncommand = ["x"]\nstartup_timeout = 3\n'
        )
        self.assertEqual(config.programs["x"].timeout(config.settings), 3.0)

    def test_unknown_rules_have_safe_defaults(self) -> None:
        config = parse_config(MINIMAL)
        self.assertFalse(config.unknown.allow)
        self.assertEqual(config.unknown.ready_check, "process")
        self.assertFalse(config.settings.allow_unknown_programs)

    def test_summary_mentions_entries(self) -> None:
        summary = "\n".join(parse_config(DEFAULT_CONFIG).summary())
        self.assertIn("weston", summary)
        self.assertIn("niri", summary)


if __name__ == "__main__":
    unittest.main()
