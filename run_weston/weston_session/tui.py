"""Terminal-Oberflaeche (curses) fuer den Session Manager."""

from __future__ import annotations

import contextlib
import curses
import sys
import textwrap
from typing import Iterable, Sequence

from . import __version__, process_manager as pm
from .manager import SessionManager

STATUS_LEVEL = {
    pm.READY: "ok",
    pm.RUNNING: "info",
    pm.STARTING: "warn",
    pm.STOPPING: "warn",
    pm.FAILED: "error",
    pm.STOPPED: "dim",
}


def wrap_line(text: str, width: int) -> list[str]:
    """Bricht Text auf `width` Zeichen um (harte Grenze, keine Tabs)."""
    if width <= 1:
        return [text]
    cleaned = text.replace("\t", "    ")
    if not cleaned:
        return [""]
    chunks: list[str] = []
    for paragraph in cleaned.split("\n"):
        wrapped = textwrap.wrap(paragraph, width=width, drop_whitespace=True)
        chunks.extend(wrapped or [""])
    return chunks


def complete_input(buffer: str, candidates: Iterable[str]) -> str:
    """Tab-Vervollstaendigung fuer das letzte Wort der Eingabe."""
    if not buffer or buffer.endswith(" "):
        return buffer
    parts = buffer.split()
    if not parts:
        return buffer
    prefix = parts[-1]
    matches = sorted({candidate for candidate in candidates if candidate.startswith(prefix)})
    if not matches:
        return buffer
    if len(matches) == 1:
        parts[-1] = matches[0]
        return " ".join(parts) + " "
    common = _common_prefix(matches)
    if len(common) > len(prefix):
        parts[-1] = common
        return " ".join(parts)
    return buffer


def _common_prefix(values: Sequence[str]) -> str:
    if not values:
        return ""
    prefix = values[0]
    for value in values[1:]:
        while not value.startswith(prefix):
            prefix = prefix[:-1]
            if not prefix:
                return ""
    return prefix


class Tui:
    MIN_WIDTH = 60
    MIN_HEIGHT = 18

    def __init__(self, manager: SessionManager) -> None:
        self.manager = manager
        self.buffer = ""
        self.history: list[str] = []
        self.history_index = 0
        self.scroll = 0
        self.done = False
        self.screen: curses.window | None = None
        self.attrs: dict[str, int] = {
            key: 0
            for key in ("accent", "ok", "warn", "error", "cmd", "dim", "bold")
        }

    # -- Lebenszyklus ----------------------------------------------------

    def run(self) -> int:
        if not sys.stdout.isatty():
            print(
                "run_weston braucht ein interaktives Terminal. "
                "Nicht-interaktiv: run_weston doctor | status | check",
                file=sys.stderr,
            )
            return 1
        try:
            curses.wrapper(self._loop)
        except KeyboardInterrupt:
            pass
        finally:
            self.manager.shutdown()
        return 0

    def _loop(self, stdscr: curses.window) -> None:
        self.screen = stdscr
        with contextlib.suppress(curses.error):
            curses.curs_set(1)
            curses.use_default_colors()
            curses.start_color()
            curses.init_pair(1, curses.COLOR_GREEN, -1)
            curses.init_pair(2, curses.COLOR_YELLOW, -1)
            curses.init_pair(3, curses.COLOR_RED, -1)
            curses.init_pair(4, curses.COLOR_CYAN, -1)
            curses.init_pair(5, curses.COLOR_BLUE, -1)
            self.attrs.update(
                {
                    "ok": curses.color_pair(1),
                    "warn": curses.color_pair(2),
                    "error": curses.color_pair(3) | curses.A_BOLD,
                    "cmd": curses.color_pair(4),
                    "accent": curses.color_pair(5),
                    "bold": curses.A_BOLD,
                    "dim": curses.A_DIM,
                }
            )
        stdscr.keypad(True)
        stdscr.timeout(150)

        while not self.done and not self.manager.exit_requested:
            self.manager.tick()
            try:
                self._draw()
            except curses.error:
                pass
            try:
                key = stdscr.get_wch()
            except curses.error:
                continue
            if key in (-1, None):
                continue
            self._handle_key(key)

    # -- Zeichnen --------------------------------------------------------

    def _put(self, y: int, x: int, text: str, attr: int = 0) -> None:
        if self.screen is None or not text:
            return
        height, width = self.screen.getmaxyx()
        if y < 0 or y >= height or x < 0 or x >= width:
            return
        available = width - x
        if y == height - 1:
            available -= 1
        if available <= 0:
            return
        with contextlib.suppress(curses.error):
            self.screen.addstr(y, x, text[:available], attr)

    def _draw(self) -> None:
        scr = self.screen
        if scr is None:
            return
        scr.erase()
        height, width = scr.getmaxyx()
        if height < self.MIN_HEIGHT or width < self.MIN_WIDTH:
            self._put(
                0,
                0,
                f"Terminal zu klein ({width}x{height}) - mindestens "
                f"{self.MIN_WIDTH}x{self.MIN_HEIGHT} noetig",
                self.attrs["error"],
            )
            scr.refresh()
            return

        accent = self.attrs["accent"]
        left_width = max(30, min(int(width * 0.46), 64))
        right_width = width - left_width - 3
        body_rows = height - 9

        title = f" WESTON SESSION MANAGER {__version__} "
        self._put(0, 0, "╔" + "═" * (width - 2) + "╗", accent)
        self._put(1, 0, "║", accent)
        self._put(1, 1, title.center(width - 2)[: width - 2], accent | curses.A_BOLD)
        self._put(1, width - 1, "║", accent)
        self._put(2, 0, "╠" + "═" * (width - 2) + "╣", accent)

        status = self._status_text()[: width - 2]
        self._put(3, 0, "║", accent)
        self._put(3, 1, status.center(width - 2)[: width - 2], accent)
        self._put(3, width - 1, "║", accent)
        self._put(
            4,
            0,
            "╠" + "═" * (left_width - 1) + "╦" + "═" * (width - left_width - 2) + "╣",
            accent,
        )

        left_lines = self._process_lines(left_width - 2)
        right_lines = self._output_lines(right_width, body_rows)
        for offset in range(body_rows):
            y = 5 + offset
            left_text, left_attr = (
                left_lines[offset] if offset < len(left_lines) else ("", 0)
            )
            right_text, right_attr = (
                right_lines[offset] if offset < len(right_lines) else ("", 0)
            )
            self._put(y, 0, "║", accent)
            self._put(y, 1, left_text.ljust(left_width - 2)[: left_width - 2], left_attr)
            self._put(y, left_width, "║", accent)
            self._put(
                y,
                left_width + 1,
                right_text.ljust(right_width)[:right_width],
                right_attr,
            )
            self._put(y, width - 1, "║", accent)

        self._put(
            height - 4,
            0,
            "╠" + "═" * (left_width - 1) + "╩" + "═" * (width - left_width - 2) + "╣",
            accent,
        )

        prompt = "> "
        available = width - 4
        visible = self.buffer[-available:] if len(self.buffer) > available else self.buffer
        input_text = (prompt + visible).ljust(width - 2)[: width - 2]
        self._put(height - 3, 0, "║", accent)
        self._put(height - 3, 1, input_text, self.attrs["bold"])
        self._put(height - 3, width - 1, "║", accent)
        cursor_x = 1 + len(prompt) + len(visible)
        with contextlib.suppress(curses.error):
            scr.move(height - 3, min(cursor_x, width - 2))

        footer = (
            " [k NUM] Kill   [logs NUM] Logs   [refresh] Status   "
            "[PgUp/PgDn] Scroll   [Tab] Ergänzen   [exit] Beenden"
        )
        self._put(height - 2, 0, "║", accent)
        self._put(height - 2, 1, footer.ljust(width - 2)[: width - 2], self.attrs["dim"])
        self._put(height - 2, width - 1, "║", accent)
        self._put(height - 1, 0, "╚" + "═" * (width - 2) + "╝", accent)
        scr.refresh()

    def _status_text(self) -> str:
        parts: list[str] = []
        for name in ("weston", "niri"):
            if self.manager.config.entry(name) is not None:
                proc = self.manager.procs.get(name)
                parts.append(f"{name.upper()}: {proc.status if proc else '-'}")
        display = self.manager.active_display()
        parts.append(f"ACTIVE DISPLAY: {display or '-'}")
        parts.append(f"PROZESSE: {len(self.manager.rows())}")
        return "    ".join(parts)

    def _process_lines(self, width: int) -> list[tuple[str, int]]:
        lines: list[tuple[str, int]] = [
            ("PROZESSE", self.attrs["bold"] | self.attrs["accent"])
        ]
        rows = self.manager.rows()
        if not rows:
            lines.append(("  (keine gestartet)", self.attrs["dim"]))
        for index, proc in enumerate(rows, start=1):
            marker = "*" if proc.external else " "
            text = f"[{index:02d}] {proc.name[:14]:<14} {proc.status:<8}{marker}"
            if proc.socket:
                text += f" {proc.socket}"
            attr = self.attrs.get(STATUS_LEVEL.get(proc.status, "info"), 0)
            lines.append((text[:width], attr))
        lines.append(("", 0))
        lines.append(("* = extern, wird nicht beendet", self.attrs["dim"]))
        return lines

    def _output_lines(self, width: int, height: int) -> list[tuple[str, int]]:
        rendered: list[tuple[str, int]] = []
        for message in self.manager.snapshot_messages():
            attr = self.attrs.get(
                {"info": "info", "ok": "ok", "warn": "warn", "error": "error", "cmd": "cmd"}.get(
                    message.level, "info"
                ),
                0,
            )
            for chunk in wrap_line(message.text, max(10, width)):
                rendered.append((chunk, attr))
        if not rendered:
            rendered = [
                ("Noch keine Ausgabe - 'help' zeigt alle Kommandos.", self.attrs["dim"])
            ]
        max_scroll = max(0, len(rendered) - height)
        scroll = min(max(0, self.scroll), max_scroll)
        end = len(rendered) - scroll
        start = max(0, end - height)
        return [
            (text[:width], attr)
            for text, attr in rendered[start:end]
        ]

    # -- Eingabe ---------------------------------------------------------

    def _handle_key(self, key) -> None:
        if isinstance(key, str):
            if key in ("\n", "\r"):
                self._submit()
            elif key in ("\x7f", "\b"):
                self.buffer = self.buffer[:-1]
            elif key == "\t":
                self.buffer = complete_input(
                    self.buffer, self.manager.completion_candidates()
                )
            elif key == "\x03":
                self.manager.emit(
                    "warn",
                    "Ctrl+C ignoriert - mit 'exit' beenden. "
                    "Laufende Prozesse werden nur ueber 'k NUM' beendet.",
                )
            elif key == "\x0c":
                self.scroll = 0
            elif key.isprintable():
                self.buffer = (self.buffer + key)[:512]
            return

        if key == curses.KEY_UP:
            self._history(-1)
        elif key == curses.KEY_DOWN:
            self._history(1)
        elif key in (curses.KEY_BACKSPACE, curses.KEY_DC):
            self.buffer = self.buffer[:-1]
        elif key == curses.KEY_PPAGE:
            self.scroll += 10
        elif key == curses.KEY_NPAGE:
            self.scroll = max(0, self.scroll - 10)
        elif key == curses.KEY_HOME:
            self.scroll = 10**6
        elif key == curses.KEY_END:
            self.scroll = 0
        elif key == curses.KEY_RESIZE:
            self.scroll = 0

    def _history(self, direction: int) -> None:
        if not self.history:
            return
        index = self.history_index + direction
        index = max(0, min(index, len(self.history)))
        self.history_index = index
        self.buffer = "" if index == len(self.history) else self.history[index]

    def _submit(self) -> None:
        text = self.buffer.strip()
        self.buffer = ""
        if not text:
            return
        if not self.history or self.history[-1] != text:
            self.history.append(text)
        self.history_index = len(self.history)
        self.scroll = 0
        self.manager.handle_command(text)
