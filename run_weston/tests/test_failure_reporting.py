"""Tests fuer Exit-Beschreibungen und den Prozessgruppen-Fallback.

Hintergrund: Wrapper wie `bwrap` (Discord) koennen sich beenden, waehrend die
App in derselben Prozessgruppe weiterlaeuft. Frueher meldete der Manager dann
FAILED, obwohl das Programm lief.
"""

from __future__ import annotations

import os
import signal
import tempfile
import time
import unittest
from pathlib import Path

from weston_session import process_manager as pm
from weston_session.config import Entry


def wait_for(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


class ExitDescriptionTest(unittest.TestCase):
    def test_regular_exit(self) -> None:
        self.assertEqual(pm.exit_description(3), "Exit 3")

    def test_signal_exit_is_named(self) -> None:
        self.assertIn("SIGTERM", pm.exit_description(-int(signal.SIGTERM)))

    def test_unknown_exit(self) -> None:
        self.assertEqual(pm.exit_description(None), "unbekannt")


class GroupFallbackTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="run-weston-group-")
        self.manager = pm.ProcessManager(Path(self.tmp.name) / "logs", kill_timeout=1)
        self.pgids: list[int] = []

    def tearDown(self) -> None:
        for pgid in self.pgids:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except OSError:
                pass
        self.tmp.cleanup()

    def start(self, script: str) -> pm.ManagedProcess:
        entry = Entry(name="wrapper", kind="programs", command=("/bin/sh", "-c", script))
        proc = self.manager.start(
            entry,
            {"PATH": os.environ.get("PATH", "/bin"), "HOME": self.tmp.name},
        )
        self.pgids.append(proc.pgid or proc.pid or 0)
        return proc

    def test_launcher_exit_with_living_group_stays_running(self) -> None:
        proc = self.start("sleep 5 & exit 0")
        self.assertTrue(wait_for(lambda: proc.popen.poll() is not None))
        self.manager.poll()
        self.assertEqual(proc.status, pm.RUNNING)
        self.assertIn("App laeuft weiter", proc.message)

    def test_finished_group_is_failed_with_exit_code(self) -> None:
        proc = self.start("exit 7")
        self.assertTrue(wait_for(lambda: proc.popen.poll() is not None))
        self.manager.poll()
        self.assertEqual(proc.status, pm.FAILED)
        self.assertIn("Exit 7", proc.message)


if __name__ == "__main__":
    unittest.main()
