"""Regressionstest: Start-Thread darf nicht zum Killer werden.

Wrapper wie `bwrap --die-with-parent` (Discord, Spotify, Steam, ...) setzen
`PR_SET_PDEATHSIG(SIGKILL)`. Der Kernel stellt dieses Signal zu, sobald der
Thread endet, der den Prozess geforkt hat. Startete die TUI eine App in einem
Worker-Thread, der nach dem Ready-Check fertig war, wurde die laufende App
sofort mit SIGKILL beendet - sichtbar als scheinbarer Absturz nach READY.
"""

from __future__ import annotations

import os
import threading
import time
import unittest

from weston_session import process_manager as pm

from tests.support import ManagerTestCase

PDEATH_BODY = """
import ctypes, signal, sys, time

libc = ctypes.CDLL(None, use_errno=True)
# PR_SET_PDEATHSIG = 1: SIGKILL, sobald der forkende Thread/Prozess endet.
libc.prctl(1, signal.SIGKILL, 0, 0, 0)

time.sleep(float(sys.argv[1]) if len(sys.argv) > 1 else 20)
"""

CONFIG = """
[settings]
startup_timeout = 3
kill_timeout = 1

[programs.pdeath]
command = ["pdeath", "20"]
ready_check = "process"
"""


class LauncherThreadTest(ManagerTestCase):
    def child_env(self) -> dict[str, str]:
        return {
            "PATH": f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}",
            "HOME": str(self.tmp),
            "XDG_RUNTIME_DIR": str(self.runtime),
        }

    def test_pdeathsig_child_survives_short_lived_start_thread(self):
        self.script("pdeath", PDEATH_BODY)
        manager = self.manager(CONFIG)
        entry = manager.config.entry("pdeath")

        holder: dict[str, object] = {}

        def worker() -> None:
            holder["proc"] = manager.procs.start(entry, self.child_env())
            # Der Thread muss das Arming von PDEATHSIG ueberleben, sonst
            # waere der Test auch mit dem alten Fehler gruen.
            time.sleep(0.8)

        thread = threading.Thread(target=worker, name="worker")
        thread.start()
        thread.join()

        proc = holder["proc"]
        time.sleep(1.5)
        manager.tick()

        self.assertIsNotNone(proc.popen)
        self.assertTrue(
            proc.alive(),
            f"Kind starb mit dem Start-Thread: status={proc.status} "
            f"exit={proc.exit_code}",
        )
        self.assertIn(proc.status, {pm.STARTING, pm.RUNNING, pm.READY})

    def test_launcher_thread_stays_alive_between_starts(self):
        self.script("pdeath", PDEATH_BODY)
        manager = self.manager(CONFIG)
        entry = manager.config.entry("pdeath")

        manager.procs.start(entry, self.child_env())
        launcher = manager.procs._launcher
        self.assertIsNotNone(launcher)
        time.sleep(0.5)
        self.assertTrue(launcher.is_alive(), "Launcher-Thread ist beendet")

        second = manager.config.entry("pdeath")
        with self.assertRaises(RuntimeError):
            manager.procs.start(second, self.child_env())


if __name__ == "__main__":
    unittest.main()
