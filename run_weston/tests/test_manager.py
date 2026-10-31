"""Ende-zu-Ende-Tests der Manager-Logik mit echten Hilfsprozessen."""

from __future__ import annotations

import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

from weston_session import compositor_manager as cm
from weston_session import process_manager as pm

from tests.support import (
    ARGDUMP_BODY,
    NIRI_BODY,
    SLEEPER_BODY,
    WESTON_BODY,
    ManagerTestCase,
    kill_group,
    wait_for,
    wait_message,
    wait_status,
)


class ManagerTestCaseBase(ManagerTestCase):
    def weston_setup(self, *, weston_body: str = WESTON_BODY, start_timeout: float = 6.0):
        self.counter += 1
        weston_name = f"fakeweston{self.counter}"
        self.script(weston_name, weston_body)
        socket_name = self.unique("wayland-w")
        config = f"""
[settings]
default_compositor = "weston"
startup_timeout = {start_timeout}
kill_timeout = 1
reuse_existing = true
stop_on_exit = true

[compositors.weston]
command = ["{weston_name}", "--backend=x11", "--socket={socket_name}"]
socket = "{socket_name}"
backend = "x11"
socket_detection = "cmdline"
ready_check = "socket"
startup_timeout = {start_timeout}
stop_on_exit = false
"""
        return weston_name, socket_name, config.strip() + "\n"

    def sleeper(self, seconds: float = 30) -> str:
        self.counter += 1
        name = f"sleeper{self.counter}"
        self.script(name, SLEEPER_BODY)
        return name


class WestonLifecycleTest(ManagerTestCaseBase):
    def test_weston_is_started_and_ready(self) -> None:
        weston_name, socket_name, config = self.weston_setup()
        manager = self.manager(config)

        self.assertTrue(manager.start("weston"))
        proc = wait_status(manager, "weston", {pm.READY})

        self.assertIsNotNone(proc, "Weston wurde nicht bereit")
        self.assertFalse(proc.external)
        self.assertEqual(proc.socket, socket_name)
        self.assertTrue((self.runtime / socket_name).exists())
        self.assertEqual(len(cm.find_running_instances(weston_name, self.runtime)), 1)
        self.assertIn("gestartet", " ".join(m.text for m in manager.snapshot_messages()))

    def test_existing_weston_is_reused_not_duplicated(self) -> None:
        weston_name, socket_name, config = self.weston_setup()
        script = self.bin / weston_name
        popen = subprocess.Popen(
            [str(script), f"--socket={socket_name}"],
            env={**os.environ, "XDG_RUNTIME_DIR": str(self.runtime)},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self.extra_pids.append(popen.pid)
        self.assertTrue(wait_for(lambda: (self.runtime / socket_name).exists()))

        manager = self.manager(config)
        manager.start("weston")
        proc = wait_status(manager, "weston", {pm.READY})

        self.assertIsNotNone(proc)
        self.assertTrue(proc.external)
        self.assertEqual(proc.pid, popen.pid)
        self.assertEqual(proc.socket, socket_name)
        self.assertEqual(len(cm.find_running_instances(weston_name, self.runtime)), 1)

    def test_failed_weston_blocks_dependent_program(self) -> None:
        _, _, config = self.weston_setup(weston_body="import sys\nsys.exit(3)\n")
        niri_name = self.name_script(NIRI_BODY)
        config += f"""
[programs.niri]
command = ["{niri_name}"]
requires = ["weston"]
ready_check = "process"
startup_timeout = 3
"""
        manager = self.manager(config)
        manager.start("niri")

        self.assertTrue(wait_message(manager, "Abhaengigkeit 'weston' ist nicht bereit", 15))
        weston = manager.procs.get("weston")
        self.assertIsNotNone(weston)
        self.assertEqual(weston.status, pm.FAILED)
        self.assertIsNone(manager.procs.get("niri"))
        self.assertFalse((self.runtime / "wayland-niri").exists())

    def name_script(self, body: str) -> str:
        self.counter += 1
        name = f"fakehelper{self.counter}"
        self.script(name, body)
        return name


class DependencyChainTest(ManagerTestCaseBase):
    def chain(self) -> tuple[str, str, str, str, str]:
        weston_name, weston_socket, config = self.weston_setup()
        self.counter += 1
        niri_name = f"fakeniri{self.counter}"
        self.script(niri_name, NIRI_BODY)
        niri_socket = self.unique("wayland-n")
        self.counter += 1
        dms_name = f"fakedms{self.counter}"
        self.script(dms_name, SLEEPER_BODY)
        config += f"""
[programs.niri]
command = ["{niri_name}"]
requires = ["weston"]
env = {{ FAKE_NIRI_SOCKET = "{niri_socket}" }}
socket_detection = "log"
ready_pattern = 'listening on Wayland socket: (?P<socket>\\S+)'
ready_check = "log"
startup_timeout = 6

[programs.dms]
command = ["{dms_name}", "30"]
requires = ["niri"]
ready_check = "process"
startup_timeout = 6
"""
        return weston_socket, niri_name, niri_socket, dms_name, config

    def test_dms_starts_after_niri_inside_weston(self) -> None:
        weston_socket, niri_name, niri_socket, dms_name, config = self.chain()
        manager = self.manager(config)
        manager.start("dms")

        dms = wait_status(manager, "dms", {pm.READY}, timeout=20)
        self.assertIsNotNone(dms, "DMS wurde nicht bereit")
        niri = wait_status(manager, "niri", {pm.READY}, timeout=10)
        weston = wait_status(manager, "weston", {pm.READY}, timeout=10)
        self.assertIsNotNone(niri)
        self.assertIsNotNone(weston)

        # Niri bekommt die Weston-Umgebung.
        self.assertEqual(niri.env["WAYLAND_DISPLAY"], weston_socket)
        self.assertEqual(niri.env["XDG_RUNTIME_DIR"], str(self.runtime))
        self.assertEqual(niri.socket, niri_socket)

        # DMS bekommt die dynamisch ermittelte Niri-Umgebung.
        self.assertEqual(dms.env["WAYLAND_DISPLAY"], niri_socket)
        self.assertEqual(dms.env["XDG_RUNTIME_DIR"], str(self.runtime))
        self.assertEqual(dms.requires, ["niri"])

        # Reihenfolge: Weston -> Niri -> DMS
        self.assertLessEqual(weston.started_at, niri.started_at)
        self.assertLessEqual(niri.started_at, dms.started_at)
        self.assertEqual(manager.active_display(), niri_socket)

    def test_niri_is_not_started_twice(self) -> None:
        _, niri_name, _, _, config = self.chain()
        manager = self.manager(config)
        manager.start("niri")
        niri = wait_status(manager, "niri", {pm.READY}, timeout=15)
        self.assertIsNotNone(niri)
        first_pid = niri.pid

        manager.start("niri")
        time.sleep(0.5)

        rows = [row for row in manager.rows() if row.name == "niri"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].pid, first_pid)

    def test_existing_niri_is_detected_with_its_socket(self) -> None:
        weston_name, weston_socket, config = self.weston_setup()
        self.counter += 1
        niri_name = f"fakeniri{self.counter}"
        script = self.script(niri_name, NIRI_BODY)
        niri_socket = self.unique("wayland-running")
        popen = subprocess.Popen(
            [str(script)],
            env={
                **os.environ,
                "XDG_RUNTIME_DIR": str(self.runtime),
                "FAKE_NIRI_SOCKET": niri_socket,
            },
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self.extra_pids.append(popen.pid)
        self.assertTrue(wait_for(lambda: (self.runtime / niri_socket).exists()))

        config += f"""
[programs.niri]
command = ["{niri_name}"]
requires = ["weston"]
socket_detection = "log"
ready_pattern = 'listening on Wayland socket: (?P<socket>\\S+)'
ready_check = "log"
startup_timeout = 6
"""
        manager = self.manager(config)
        manager.start("niri")
        niri = wait_status(manager, "niri", {pm.READY}, timeout=15)

        self.assertIsNotNone(niri)
        self.assertTrue(niri.external)
        self.assertEqual(niri.pid, popen.pid)
        self.assertEqual(niri.socket, niri_socket)
        self.assertEqual(len(cm.find_running_instances(niri_name, self.runtime)), 1)


class CommandTest(ManagerTestCaseBase):
    def test_unknown_program_is_refused(self) -> None:
        _, _, config = self.weston_setup()
        manager = self.manager(config)
        manager.handle_command("gibtsnicht-xyz")

        self.assertTrue(wait_message(manager, "Unbekanntes Programm", 3))
        self.assertEqual(manager.rows(), [])
        self.assertTrue(
            any("allow_unknown_programs" in m.text for m in manager.snapshot_messages())
        )

    def test_run_env_starts_program_in_weston_environment(self) -> None:
        _, weston_socket, config = self.weston_setup()
        manager = self.manager(config)
        manager.start("weston")
        self.assertIsNotNone(wait_status(manager, "weston", {pm.READY}))

        sleeper = self.sleeper(30)
        script_path = str(self.bin / sleeper)
        manager.handle_command(f"run --env weston {script_path} 30")

        proc = wait_status(manager, sleeper, {pm.READY}, timeout=10)
        self.assertIsNotNone(proc)
        self.assertEqual(proc.env["WAYLAND_DISPLAY"], weston_socket)
        self.assertEqual(proc.env["XDG_RUNTIME_DIR"], str(self.runtime))

    def test_kill_by_index_stops_process(self) -> None:
        _, _, config = self.weston_setup()
        sleeper = self.sleeper(30)
        config += f"""
[programs.arbeit]
command = ["{sleeper}", "30"]
ready_check = "process"
"""
        manager = self.manager(config)
        manager.start("arbeit")
        proc = wait_status(manager, "arbeit", {pm.READY})
        self.assertIsNotNone(proc)

        manager.handle_command("k 1")
        stopped = wait_status(manager, "arbeit", {pm.STOPPED}, timeout=10)
        self.assertIsNotNone(stopped)
        self.assertFalse(pm.pid_alive(proc.pid))

    def test_tui_stays_responsive_while_program_starts(self) -> None:
        _, _, config = self.weston_setup()
        slow = self.sleeper(20)
        config += f"""
[programs.langsam]
command = ["{slow}", "20"]
ready_check = "socket"
socket = "taucht-nie-auf"
startup_timeout = 5
"""
        manager = self.manager(config)

        started = time.monotonic()
        manager.handle_command("langsam")
        self.assertLess(time.monotonic() - started, 0.5)

        self.assertIsNotNone(wait_for(lambda: manager.procs.get("langsam"), 3))

        status_start = time.monotonic()
        manager.handle_command("status")
        manager.handle_command("help")
        self.assertLess(time.monotonic() - status_start, 0.5)

        messages = " ".join(message.text for message in manager.snapshot_messages())
        self.assertIn("Prozesse:", messages)
        self.assertIn("Kommandos:", messages)
        self.assertIn(manager.procs.get("langsam").status, (pm.STARTING, pm.RUNNING, pm.FAILED))

    def test_arguments_are_not_shell_evaluated(self) -> None:
        _, _, config = self.weston_setup()
        self.counter += 1
        name = f"argdump{self.counter}"
        self.script(name, ARGDUMP_BODY)
        output = self.tmp / "args.txt"
        config += f"""
[programs.argdump]
command = ["{name}", "{output}"]
ready_check = "exit"
startup_timeout = 5
"""
        manager = self.manager(config)
        manager.start("argdump", ("a;b", "$(id)", "`id`"))
        self.assertTrue(wait_for(lambda: output.exists(), 8))

        self.assertEqual(output.read_text(encoding="utf-8").splitlines(), ["a;b", "$(id)", "`id`"])


class LifecycleTest(ManagerTestCaseBase):
    def test_shutdown_stops_managed_programs_but_keeps_weston(self) -> None:
        _, socket_name, config = self.weston_setup()
        sleeper = self.sleeper(30)
        config += f"""
[programs.arbeit]
command = ["{sleeper}", "30"]
ready_check = "process"
"""
        manager = self.manager(config)
        manager.start("weston")
        weston = wait_status(manager, "weston", {pm.READY})
        manager.start("arbeit")
        arbeit = wait_status(manager, "arbeit", {pm.READY})
        self.assertIsNotNone(weston)
        self.assertIsNotNone(arbeit)

        stopped = manager.shutdown()

        self.assertIn("arbeit", stopped)
        self.assertEqual(manager.procs.get("arbeit").status, pm.STOPPED)
        self.assertTrue(weston.alive(), "Weston mit stop_on_exit = false muss weiterlaufen")
        self.assertTrue((self.runtime / socket_name).exists())

    def test_adopts_process_from_previous_run(self) -> None:
        _, _, config = self.weston_setup()
        first = self.manager(config)
        first.start("weston")
        weston = wait_status(first, "weston", {pm.READY})
        self.assertIsNotNone(weston)
        first.shutdown()
        self.assertTrue(weston.alive())

        second = self.manager(config)
        adopted = second.procs.get("weston")
        self.assertIsNotNone(adopted)
        self.assertTrue(adopted.external)
        self.assertEqual(adopted.pid, weston.pid)
        self.assertTrue(wait_message(second, "uebernommen", 3))

        kill_group(weston.pid)

    def test_reload_keeps_old_config_when_new_one_is_broken(self) -> None:
        _, _, config = self.weston_setup()
        manager = self.manager(config)
        self.assertTrue(manager.reload_config())

        self.paths.config.write_text("[compositors.weston]\ncommand = \n", encoding="utf-8")
        self.assertFalse(manager.reload_config())
        self.assertIn("weston", manager.config.compositors)
        self.assertTrue(wait_message(manager, "NICHT geladen", 3))

    def test_doctor_returns_results(self) -> None:
        _, _, config = self.weston_setup()
        manager = self.manager(config)
        results = manager.doctor()

        self.assertTrue(results)
        self.assertTrue(any(item.name == "Konfiguration" for item in results))
        levels = {item.level for item in results}
        self.assertTrue(levels <= {"ok", "warn", "fail"})

    def test_status_and_logs_commands(self) -> None:
        _, _, config = self.weston_setup()
        manager = self.manager(config)
        manager.start("weston")
        self.assertIsNotNone(wait_status(manager, "weston", {pm.READY}))

        manager.handle_command("status")
        manager.handle_command("logs 1")
        messages = " ".join(message.text for message in manager.snapshot_messages())
        self.assertIn("Aktives Display", messages)
        self.assertIn("WAYLAND_DISPLAY", messages)


if __name__ == "__main__":
    unittest.main()
