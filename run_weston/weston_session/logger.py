"""Logdateien des Managers und der gestarteten Prozesse."""

from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path

_SAFE = re.compile(r"[^A-Za-z0-9_.+-]+")


def safe_name(name: str) -> str:
    cleaned = _SAFE.sub("_", name).strip("._") or "unnamed"
    return cleaned


def tail_lines(path: str | Path, count: int = 40) -> list[str]:
    """Die letzten `count` Zeilen einer Datei (leer, wenn sie fehlt)."""
    if count <= 0:
        return []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return []
    return lines[-count:]


def read_from(path: str | Path, offset: int) -> tuple[str, int]:
    """Liest neue Zeichen ab `offset`; liefert Text und neuen Offset."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            handle.seek(offset)
            return handle.read(), handle.tell()
    except OSError:
        return "", offset


class SessionLogger:
    """Schreibt das Manager-Log und kennt die Logpfade aller Eintraege."""

    def __init__(self, log_dir: str | Path, name: str = "manager") -> None:
        self.log_dir = Path(log_dir)
        self.name = safe_name(name)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.log_dir / f"{self.name}.log"

    def path_for(self, name: str) -> Path:
        return self.log_dir / f"{safe_name(name)}.log"

    def log(self, level: str, message: str) -> None:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"{stamp} {level:<5} {message}\n"
        try:
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(line)
                handle.flush()
        except OSError:
            pass

    def info(self, message: str) -> None:
        self.log("INFO", message)

    def warn(self, message: str) -> None:
        self.log("WARN", message)

    def error(self, message: str) -> None:
        self.log("ERROR", message)

    def tail(self, name: str = "manager", count: int = 40) -> list[str]:
        return tail_lines(self.path_for(name), count)

    def tail_path(self, path: str | Path, count: int = 40) -> list[str]:
        return tail_lines(path, count)
