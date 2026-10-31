"""Die Pruefungen hinter dem Kommando `doctor`."""

from __future__ import annotations

import importlib.util
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import compositor_manager as compositors
from .config import ConfigError, parse_config

REQUIRED_MODULES = ("tomllib", "curses", "fcntl", "socket", "subprocess", "json")


@dataclass(frozen=True)
class Diagnostic:
    level: str  # ok | warn | fail
    name: str
    detail: str

    def line(self) -> str:
        marker = {"ok": "✓", "warn": "!", "fail": "✗"}.get(self.level, "?")
        return f"[{marker}] {self.name}: {self.detail}"


def _display_socket(display: str) -> Path | None:
    if not display.startswith(":"):
        return None
    number = display[1:].split(".", 1)[0]
    if not number.isdigit():
        return None
    return Path(f"/tmp/.X11-unix/X{number}")


def run_doctor(
    manager,
    which: Callable[[str], str | None] = shutil.which,
    environ: dict[str, str] | None = None,
) -> list[Diagnostic]:
    env = os.environ if environ is None else environ
    runtime = Path(manager.paths.runtime)
    results: list[Diagnostic] = []

    def add(level: str, name: str, detail: str) -> None:
        results.append(Diagnostic(level, name, detail))

    # Programme
    for program in ("weston", "niri", "dms"):
        path = which(program)
        if path:
            add("ok", program, path)
        else:
            add("warn" if program == "dms" else "warn", program, "nicht im PATH")

    # Runtime-Verzeichnis
    if runtime.is_dir():
        mode = runtime.stat().st_mode & 0o777
        owner = runtime.stat().st_uid
        add("ok", "XDG_RUNTIME_DIR", f"{runtime} (Modus {mode:o}, UID {owner})")
        if owner != os.getuid():
            add("fail", "Runtime-Besitzer", f"UID {owner} statt {os.getuid()}")
        if mode & 0o077:
            add("warn", "Runtime-Rechte", f"Modus {mode:o} ist weiter als 700")
    else:
        add("fail", "XDG_RUNTIME_DIR", f"{runtime} fehlt")

    # X11
    display = env.get("DISPLAY", "")
    if display:
        x_socket = _display_socket(display)
        if x_socket is None or x_socket.exists():
            add("ok", "X11-Display", display)
        else:
            add("warn", "X11-Display", f"DISPLAY={display}, aber {x_socket} fehlt")
    else:
        add("warn", "X11-Display", "DISPLAY ist nicht gesetzt (Weston braucht X11)")

    # Compositoren
    config = manager.config
    managed = {proc.name for proc in manager.procs.rows()}
    for entry in config.compositors.values():
        executable = Path(entry.command[0]).name
        instances = compositors.find_running_instances(
            executable, runtime, proc_root=manager.proc_root
        )
        usable = [
            instance
            for instance in instances
            if instance.socket
            and compositors.probe_unix_socket(runtime / instance.socket)
        ]
        if usable:
            detail = ", ".join(f"PID {i.pid} -> {i.socket}" for i in usable)
            add("ok", entry.name, f"laeuft: {detail}")
        elif instances:
            add(
                "warn",
                entry.name,
                f"laeuft (PID {instances[0].pid}), aber kein Socket erreichbar",
            )
            if entry.name in managed:
                add("warn", entry.name, "wird vom Manager verwaltet")
        else:
            add("ok", entry.name, "nicht aktiv (kann gestartet werden)")
        sockets = {instance.socket for instance in instances if instance.socket}
        if len(sockets) > 1:
            add(
                "warn",
                f"{entry.name} Duplikate",
                f"{len(sockets)} Instanzen mit eigenem Socket "
                f"({', '.join(sorted(sockets))}) - die neueste wird verwendet",
            )

    # Konfiguration
    try:
        parsed = parse_config(config.raw, config.path)
    except ConfigError as exc:
        add("fail", "Konfiguration", "; ".join(exc.errors))
    else:
        add("ok", "Konfiguration", f"{parsed.path} gueltig")
        for warning in parsed.warnings:
            add("warn", "Konfiguration", warning)

    # Python
    missing = [name for name in REQUIRED_MODULES if importlib.util.find_spec(name) is None]
    if missing:
        add("fail", "Python-Module", f"fehlen: {', '.join(missing)}")
    else:
        add("ok", "Python-Module", ", ".join(REQUIRED_MODULES))

    # Logverzeichnis beschreibbar
    try:
        with tempfile.NamedTemporaryFile(dir=manager.paths.logs, prefix=".doctor-"):
            pass
        add("ok", "Logverzeichnis", str(manager.paths.logs))
    except OSError as exc:
        add("fail", "Logverzeichnis", f"{manager.paths.logs} nicht beschreibbar: {exc}")

    return results
