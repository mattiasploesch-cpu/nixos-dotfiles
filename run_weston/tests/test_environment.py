"""Tests fuer Abhaengigkeitsaufloesung und Umgebungsaufbau."""

from __future__ import annotations

import unittest

from weston_session.config import Entry
from weston_session.environment_manager import EnvironmentManager
from weston_session.resolver import ResolverError, find_cycles, resolve_order


def entry(name: str, requires=(), kind: str = "programs") -> Entry:
    return Entry(name=name, kind=kind, command=(name,), requires=tuple(requires))


class ResolverTest(unittest.TestCase):
    def setUp(self) -> None:
        self.entries = {
            "weston": entry("weston", kind="compositors"),
            "niri": entry("niri", ("weston",)),
            "dms": entry("dms", ("niri",)),
        }

    def test_dependencies_come_first(self) -> None:
        order = [item.name for item in resolve_order(self.entries, "dms")]
        self.assertEqual(order, ["weston", "niri", "dms"])

    def test_multiple_dependencies_are_deduplicated(self) -> None:
        entries = dict(self.entries)
        entries["werkzeug"] = entry("werkzeug", ("weston", "niri"))
        order = [item.name for item in resolve_order(entries, "werkzeug")]
        self.assertEqual(order, ["weston", "niri", "werkzeug"])

    def test_program_may_require_program(self) -> None:
        entries = dict(self.entries)
        entries["panel"] = entry("panel", ("dms",))
        order = [item.name for item in resolve_order(entries, "panel")]
        self.assertEqual(order, ["weston", "niri", "dms", "panel"])

    def test_missing_dependency_is_explained(self) -> None:
        with self.assertRaises(ResolverError) as context:
            resolve_order(self.entries, "unbekannt")
        self.assertIn("Unbekannter Eintrag", str(context.exception))

    def test_cycle_raises_readable_error(self) -> None:
        entries = {
            "a": entry("a", ("b",)),
            "b": entry("b", ("a",)),
        }
        with self.assertRaises(ResolverError) as context:
            resolve_order(entries, "a")
        self.assertIn("Zyklische Abhaengigkeit", str(context.exception))
        self.assertIn("a -> b -> a", str(context.exception))

    def test_find_cycles_returns_chain(self) -> None:
        cycles = find_cycles({"a": entry("a", ("b",)), "b": entry("b", ("a",))})
        self.assertEqual(len(cycles), 1)
        self.assertEqual(cycles[0][0], cycles[0][-1])

    def test_linear_graph_has_no_cycle(self) -> None:
        self.assertEqual(find_cycles(self.entries), [])


class EnvironmentTest(unittest.TestCase):
    def setUp(self) -> None:
        self.manager = EnvironmentManager(
            "/run/user/1000",
            base_env={
                "PATH": "/run/current-system/sw/bin",
                "HOME": "/home/administrator",
                "DISPLAY": ":0",
                "WAYLAND_DISPLAY": "wayland-geerbt",
                "XDG_RUNTIME_DIR": "/falsch",
                "UNWICHTIG": "wird gefiltert",
            },
        )

    def test_base_environment_uses_runtime_dir(self) -> None:
        env = self.manager.base()
        self.assertEqual(env["XDG_RUNTIME_DIR"], "/run/user/1000")
        self.assertEqual(env["DISPLAY"], ":0")
        self.assertNotIn("WAYLAND_DISPLAY", env)
        self.assertNotIn("UNWICHTIG", env)

    def test_niri_gets_weston_socket(self) -> None:
        env = self.manager.for_entry(["niri"], wayland_display="wayland-weston")
        self.assertEqual(env["XDG_RUNTIME_DIR"], "/run/user/1000")
        self.assertEqual(env["WAYLAND_DISPLAY"], "wayland-weston")

    def test_dms_gets_niri_socket(self) -> None:
        env = self.manager.for_entry(["dms", "run"], wayland_display="wayland-3")
        self.assertEqual(env["XDG_RUNTIME_DIR"], "/run/user/1000")
        self.assertEqual(env["WAYLAND_DISPLAY"], "wayland-3")
        self.assertEqual(env["PATH"], "/run/current-system/sw/bin")

    def test_x11_backend_strips_inherited_wayland_display(self) -> None:
        env = self.manager.for_entry(
            ["weston", "--backend=x11"],
            backend="x11",
            wayland_display="wayland-3",
        )
        self.assertNotIn("WAYLAND_DISPLAY", env)
        self.assertEqual(env["DISPLAY"], ":0")

    def test_extra_environment_wins(self) -> None:
        env = self.manager.for_entry(
            ["x"],
            wayland_display="wayland-1",
            extra_env={"FAKE_NIRI_SOCKET": "wayland-9", "WAYLAND_DISPLAY": "wayland-7"},
        )
        self.assertEqual(env["FAKE_NIRI_SOCKET"], "wayland-9")
        self.assertEqual(env["WAYLAND_DISPLAY"], "wayland-7")

    def test_runtime_dir_is_forced_even_if_overridden(self) -> None:
        env = self.manager.for_entry(["x"], extra_env={"XDG_RUNTIME_DIR": "/tmp/falsch"})
        self.assertEqual(env["XDG_RUNTIME_DIR"], "/run/user/1000")


if __name__ == "__main__":
    unittest.main()
