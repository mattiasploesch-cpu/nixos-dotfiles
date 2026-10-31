"""Gemeinsame Helfer fuer die Tests.

Die Tests starten winzige echte Prozesse (Python-Skripte mit eigenen
Unix-Sockets), aber niemals Weston, Niri oder DMS.
"""

from __future__ import annotations

import os
import shutil
import signal
import socket
import sys
import tempfile
import time
import unittest
from pathlib import Path

from weston_session.config import parse_config
from weston_session.environment_manager import EnvironmentManager
from weston_session.logger import SessionLogger
from weston_session.manager import SessionManager
from weston_session.paths import Paths

PYTHON = sys.executable


def make_script(directory: Path, name: str, body: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(f"#!{PYTHON}\n{body}\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def wait_for(predicate, timeout: float = 8.0, interval: float = 0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return predicate()


def wait_status(manager: SessionManager, name: str, statuses, timeout: float = 8.0):
    def check():
        proc = manager.procs.get(name)
        if proc is not None and proc.status in statuses:
            return proc
        return None

    return wait_for(check, timeout)


def wait_message(manager: SessionManager, needle: str, timeout: float = 8.0) -> bool:
    return bool(
        wait_for(
            lambda: any(needle in message.text for message in manager.snapshot_messages()),
            timeout,
        )
    )


def kill_group(pid: int) -> None:
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pid, sig)
        except OSError:
            try:
                os.kill(pid, sig)
            except OSError:
                pass
        time.sleep(0.05)


def listen_socket(runtime_dir: Path, name: str) -> socket.socket:
    runtime_dir.mkdir(parents=True, exist_ok=True)
    path = runtime_dir / name
    if path.exists():
        path.unlink()
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(str(path))
    sock.listen(8)
    return sock


WESTON_BODY = """
import os, socket, sys, time

name = None
for index, arg in enumerate(sys.argv):
    if arg.startswith("--socket="):
        name = arg.split("=", 1)[1]
    elif arg == "--socket" and index + 1 < len(sys.argv):
        name = sys.argv[index + 1]

name = name or "wayland-fake"
path = os.path.join(os.environ["XDG_RUNTIME_DIR"], name)
try:
    os.unlink(path)
except OSError:
    pass
sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
sock.bind(path)
sock.listen(8)
print("weston: listening on Wayland socket: " + name, flush=True)
while True:
    try:
        connection, _ = sock.accept()
        connection.close()
    except Exception:
        time.sleep(0.2)
"""

NIRI_BODY = """
import os, socket, time

name = os.environ.get("FAKE_NIRI_SOCKET", "wayland-niri")
path = os.path.join(os.environ["XDG_RUNTIME_DIR"], name)
try:
    os.unlink(path)
except OSError:
    pass
sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
sock.bind(path)
sock.listen(8)
print("listening on Wayland socket: " + name, flush=True)
while True:
    try:
        connection, _ = sock.accept()
        connection.close()
    except Exception:
        time.sleep(0.2)
"""

SLEEPER_BODY = """
import sys, time
time.sleep(float(sys.argv[1]) if len(sys.argv) > 1 else 30)
"""

ARGDUMP_BODY = """
import sys
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    for arg in sys.argv[2:]:
        handle.write(arg + "\\n")
"""


class ManagerTestCase(unittest.TestCase):
    """Basisklasse mit eigenem Runtime-/State-Verzeichnis und Aufraeumen."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="run-weston-test-"))
        self.runtime = self.tmp / "runtime"
        self.bin = self.tmp / "bin"
        self.runtime.mkdir(parents=True)
        self.bin.mkdir(parents=True)
        self.paths = Paths.from_env(
            config=self.tmp / "config.toml",
            state=self.tmp / "state",
            runtime=self.runtime,
        ).ensure()
        self.managers: list[SessionManager] = []
        self.extra_pids: list[int] = []
        self.counter = 0

    def tearDown(self) -> None:
        for manager in self.managers:
            try:
                manager.procs.stop_all()
            except Exception:  # noqa: BLE001 - Aufraeumen darf nicht scheitern
                pass
        for pid in self.extra_pids:
            kill_group(pid)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def unique(self, prefix: str = "wayland-test") -> str:
        self.counter += 1
        return f"{prefix}-{self.counter}-{os.getpid()}"

    def script(self, name: str, body: str) -> Path:
        return make_script(self.bin, name, body)

    def manager(self, text: str) -> SessionManager:
        config = parse_config(text, self.paths.config)
        self.paths.config.write_text(text, encoding="utf-8")
        env = EnvironmentManager(
            self.runtime,
            base_env={
                "PATH": f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}",
                "HOME": str(self.tmp),
                "DISPLAY": ":0",
            },
        )
        manager = SessionManager(
            paths=self.paths,
            config=config,
            env_manager=env,
            logger=SessionLogger(self.paths.logs),
        )
        self.managers.append(manager)
        return manager
