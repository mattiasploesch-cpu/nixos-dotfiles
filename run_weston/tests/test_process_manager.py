"""Tests fuer Prozessverwaltung, Status und Ready-Checks."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

from weston_session.config import Entry, Settings
from weston_session.process_manager import (
    FAILED,
    READY,
    STOPPED,
    ProcessManager,
    pid_alive,
    process_identity_matches,
    read_start_ticks,
)

from tests.support import make_script, wait_for

LOG_SOCKET_BODY = """
import os, socket, time
name = os.environ["LOG_SOCKET_NAME"]
path = os.path.join(os.environ["XDG_RUNTIME_DIR"], name)
try:
    os.unlink(path)
except OSError:
    pass
sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
sock.bind(path)
sock.listen(4)
print("listening on Wayland socket: " + name, flush=True)
time.sleep(30)
"""

SOCKET_ONLY_BODY = """
import os, socket, sys, time
path = os.path.join(os.environ["XDG_RUNTIME_DIR"], sys.argv[1])
try:
    os.unlink(path)
except OSError:
    pass
sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
sock.bind(path)
sock.listen(4)
time.sleep(30)
"""

CHILD_BODY = """
import subprocess, sys, time
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
print("CHILD", child.pid, flush=True)
time.sleep(60)
"""


class ProcessManagerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="run-weston-pm-"))
        self.runtime = self.tmp / "runtime"
        self.runtime.mkdir()
        self.bin = self.tmp / "bin"
        self.logs = self.tmp / "logs"
        self.pm = ProcessManager(self.logs, kill_timeout=1.0)
        self.settings = Settings(startup_timeout=5.0)
        self.counter = 0

    def tearDown(self) -> None:
        try:
            self.pm.stop_all()
        except Exception:  # noqa: BLE001
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- Helfer ----------------------------------------------------------

    def name(self, prefix: str) -> str:
        self.counter += 1
        return f"{prefix}-{self.counter}"

    def entry(self, command, **kwargs) -> Entry:
        name = kwargs.pop("name", self.name("proc"))
        return Entry(name=name, kind="programs", command=tuple(command), **kwargs)

    def env(self, **extra) -> dict[str, str]:
        environment = dict(os.environ)
        environment["XDG_RUNTIME_DIR"] = str(self.runtime)
        environment.update(extra)
        return environment

    def start(self, entry: Entry, **extra):
        return self.pm.start(entry, self.env(**extra))

    def script(self, body: str, name: str | None = None) -> Path:
        return make_script(self.bin, name or self.name("helper"), body)

    # -- Tests -----------------------------------------------------------

    def test_process_becomes_ready(self) -> None:
        entry = self.entry([sys.executable, "-c", "import time; time.sleep(10)"])
        proc = self.start(entry)
        self.assertTrue(self.pm.wait_ready(proc, entry, self.settings, self.runtime))
        self.assertEqual(proc.status, READY)
        self.assertEqual(proc.pid, proc.popen.pid)
        self.assertIsNotNone(proc.start_ticks)

    def test_immediate_crash_is_failed_with_exit_code(self) -> None:
        entry = self.entry([sys.executable, "-c", "import sys; sys.exit(3)"])
        proc = self.start(entry)
        self.assertFalse(self.pm.wait_ready(proc, entry, self.settings, self.runtime))
        self.assertEqual(proc.status, FAILED)
        self.assertEqual(proc.exit_code, 3)
        self.assertIn("Exit 3", proc.message)

    def test_exit_check_treats_zero_as_success(self) -> None:
        entry = self.entry([sys.executable, "-c", "pass"], ready_check="exit")
        proc = self.start(entry)
        self.assertTrue(self.pm.wait_ready(proc, entry, self.settings, self.runtime))
        self.assertEqual(proc.exit_code, 0)
        self.assertEqual(proc.status, READY)

    def test_exit_check_failure(self) -> None:
        entry = self.entry([sys.executable, "-c", "import sys; sys.exit(4)"], ready_check="exit")
        proc = self.start(entry)
        self.assertFalse(self.pm.wait_ready(proc, entry, self.settings, self.runtime))
        self.assertEqual(proc.status, FAILED)
        self.assertEqual(proc.exit_code, 4)

    def test_log_pattern_detects_dynamic_socket(self) -> None:
        socket_name = self.name("wayland-log")
        script = self.script(LOG_SOCKET_BODY)
        entry = self.entry(
            [str(script)],
            ready_check="log",
            socket_detection="log",
            ready_pattern=r"listening on Wayland socket: (?P<socket>\S+)",
        )
        proc = self.start(entry, LOG_SOCKET_NAME=socket_name)
        self.assertTrue(self.pm.wait_ready(proc, entry, self.settings, self.runtime))
        self.assertEqual(proc.status, READY)
        self.assertEqual(proc.socket, socket_name)
        self.assertTrue((self.runtime / socket_name).exists())

    def test_socket_check_waits_for_socket(self) -> None:
        socket_name = self.name("wayland-sock")
        script = self.script(SOCKET_ONLY_BODY)
        entry = self.entry(
            [str(script), socket_name], ready_check="socket", socket=socket_name
        )
        proc = self.start(entry)
        self.assertTrue(self.pm.wait_ready(proc, entry, self.settings, self.runtime))
        self.assertEqual(proc.status, READY)
        self.assertEqual(proc.socket, socket_name)

    def test_socket_check_times_out(self) -> None:
        entry = self.entry(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            ready_check="socket",
            socket="gibt-es-nicht",
            startup_timeout=0.6,
        )
        proc = self.start(entry)
        started = time.monotonic()
        self.assertFalse(self.pm.wait_ready(proc, entry, self.settings, self.runtime))
        self.assertLess(time.monotonic() - started, 4)
        self.assertEqual(proc.status, FAILED)
        self.assertIn("Timeout", proc.message)
        # Der Prozess bleibt kontrolliert verwaltet, nicht verwaist.
        self.assertTrue(proc.alive())

    def test_command_check(self) -> None:
        entry = self.entry(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            ready_check="command",
            ready_command=[sys.executable, "-c", "raise SystemExit(0)"],
        )
        proc = self.start(entry)
        self.assertTrue(self.pm.wait_ready(proc, entry, self.settings, self.runtime))
        self.assertEqual(proc.status, READY)

    def test_command_check_that_fails_times_out(self) -> None:
        entry = self.entry(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            ready_check="command",
            ready_command=[sys.executable, "-c", "raise SystemExit(1)"],
            startup_timeout=0.6,
        )
        proc = self.start(entry)
        self.assertFalse(self.pm.wait_ready(proc, entry, self.settings, self.runtime))
        self.assertEqual(proc.status, FAILED)

    def test_stop_terminates_process_group_and_child(self) -> None:
        script = self.script(CHILD_BODY)
        entry = self.entry([str(script)])
        proc = self.start(entry)
        self.assertTrue(self.pm.wait_ready(proc, entry, self.settings, self.runtime))

        def child_pid():
            lines = [line for line in self.pm.tail(entry.name, 20) if line.startswith("CHILD ")]
            return int(lines[-1].split()[1]) if lines else None

        child = wait_for(child_pid, timeout=5)
        self.assertIsNotNone(child)
        self.assertTrue(pid_alive(child))

        self.assertTrue(self.pm.stop(entry.name))
        self.assertEqual(proc.status, STOPPED)
        self.assertFalse(pid_alive(proc.pid))
        self.assertFalse(pid_alive(child))

    def test_external_instance_is_never_stopped(self) -> None:
        entry = self.entry(["nichts"])
        self.pm.register_external(entry, os.getpid(), None)
        with self.assertRaises(RuntimeError):
            self.pm.stop(entry.name)

    def test_duplicate_start_is_refused(self) -> None:
        entry = self.entry([sys.executable, "-c", "import time; time.sleep(10)"])
        self.start(entry)
        with self.assertRaises(RuntimeError):
            self.start(entry)

    def test_poll_detects_crash(self) -> None:
        entry = self.entry([sys.executable, "-c", "import time; time.sleep(0.1)"])
        proc = self.start(entry)
        time.sleep(0.5)
        self.pm.poll()
        self.assertEqual(proc.status, FAILED)
        self.assertEqual(proc.exit_code, 0)

    def test_log_contains_command_header(self) -> None:
        entry = self.entry([sys.executable, "-c", "pass"])
        proc = self.start(entry)
        time.sleep(0.3)
        head = "\n".join(self.pm.tail(entry.name, 10))
        self.assertIn("# run_weston:", head)
        self.assertIn(str(proc.log_path), str(proc.log_path))

    def test_pid_identity_guard(self) -> None:
        ticks = read_start_ticks(os.getpid())
        self.assertIsNotNone(ticks)
        self.assertTrue(process_identity_matches(os.getpid(), ticks))
        self.assertFalse(process_identity_matches(os.getpid(), (ticks or 0) + 1))
        self.assertFalse(process_identity_matches(999_999_999, None))

    def test_arguments_are_not_shell_evaluated(self) -> None:
        entry = self.entry(
            [
                sys.executable,
                "-c",
                "import sys; print('ARGS', sys.argv[1:], flush=True); import time; time.sleep(0.3)",
                "a;b",
                "$(id)",
            ]
        )
        self.start(entry)
        time.sleep(0.5)
        log = "\n".join(self.pm.tail(entry.name, 20))
        self.assertIn("'a;b'", log)
        self.assertIn("'$(id)'", log)

    def test_tail_of_unknown_process_is_empty(self) -> None:
        self.assertEqual(self.pm.tail("gibtsnicht"), [])


if __name__ == "__main__":
    unittest.main()
