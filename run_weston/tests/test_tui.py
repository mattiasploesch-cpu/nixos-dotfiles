"""Tests fuer TUI-Hilfsfunktionen (ohne curses-Screen)."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from weston_session.manager import Message
from weston_session.tui import Tui, complete_input, wrap_line


class StubProcess:
    def __init__(self, name, status="READY", socket=None, external=False):
        self.name = name
        self.status = status
        self.socket = socket
        self.external = external
        self.message = ""
        self.pid = 1


class StubManager:
    def __init__(self) -> None:
        self._rows = [StubProcess("weston", socket="wayland-weston")]
        self._messages = [
            Message("info", "gestartet"),
            Message("error", "FEHLER: kein Socket"),
            Message("cmd", "> dms run"),
        ]
        self.config = SimpleNamespace(entry=lambda name: None)
        self.procs = SimpleNamespace(get=lambda name: None)

    def rows(self):
        return self._rows

    def snapshot_messages(self):
        return self._messages

    def active_display(self):
        return "wayland-1"


class CompletionTest(unittest.TestCase):
    def test_single_match_is_completed_with_space(self) -> None:
        self.assertEqual(complete_input("doc", ["doctor", "help"]), "doctor ")

    def test_common_prefix_is_extended(self) -> None:
        self.assertEqual(complete_input("f", ["foot", "fzf"]), "f")

    def test_no_match_keeps_input(self) -> None:
        self.assertEqual(complete_input("xyz", ["foot"]), "xyz")

    def test_empty_input_is_untouched(self) -> None:
        self.assertEqual(complete_input("", ["foot"]), "")

    def test_only_last_word_is_completed(self) -> None:
        self.assertEqual(complete_input("run --env ni", ["niri", "weston"]), "run --env niri ")


class WrapTest(unittest.TestCase):
    def test_long_line_is_wrapped(self) -> None:
        chunks = wrap_line("wort " * 20, 20)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 20 for chunk in chunks))

    def test_newlines_are_split(self) -> None:
        self.assertEqual(wrap_line("a\nb", 10), ["a", "b"])

    def test_tabs_are_expanded(self) -> None:
        self.assertEqual(wrap_line("a\tb", 10), ["a    b"])

    def test_empty_text(self) -> None:
        self.assertEqual(wrap_line("", 5), [""])


class LayoutTest(unittest.TestCase):
    def test_status_text_contains_display(self) -> None:
        tui = Tui(StubManager())
        text = tui._status_text()
        self.assertIn("ACTIVE DISPLAY: wayland-1", text)
        self.assertIn("PROZESSE: 1", text)

    def test_process_lines_mark_external_instances(self) -> None:
        manager = StubManager()
        manager._rows.append(StubProcess("niri", external=True, socket="wayland-3"))
        lines = Tui(manager)._process_lines(40)
        joined = "\n".join(text for text, _ in lines)
        self.assertIn("weston", joined)
        self.assertIn("extern", joined)

    def test_output_respects_height_and_scroll(self) -> None:
        manager = StubManager()
        manager._messages = [Message("info", f"zeile {index}") for index in range(200)]
        tui = Tui(manager)
        visible = tui._output_lines(40, 10)
        self.assertLessEqual(len(visible), 10)
        self.assertIn("zeile 199", visible[-1][0])

        tui.scroll = 50
        scrolled = tui._output_lines(40, 10)
        self.assertNotIn("zeile 199", scrolled[-1][0])

    def test_long_messages_are_clipped_to_width(self) -> None:
        manager = StubManager()
        manager._messages = [Message("error", "x" * 500)]
        visible = Tui(manager)._output_lines(30, 5)
        self.assertTrue(all(len(text) <= 30 for text, _ in visible))


if __name__ == "__main__":
    unittest.main()
