"""Tests fuer Compositor-/Socket-Erkennung ohne echte Weston-Session."""

from __future__ import annotations

import os
import shutil
import socket
import tempfile
import unittest
from pathlib import Path

from weston_session import compositor_manager as cm


def fake_process(root: Path, pid: int, argv: list[str], fds: dict[str, str] | None = None) -> Path:
    directory = root / str(pid)
    (directory / "fd").mkdir(parents=True)
    (directory / "cmdline").write_bytes(b"\0".join(arg.encode() for arg in argv) + b"\0")
    for fd_name, target in (fds or {}).items():
        os.symlink(target, directory / "fd" / fd_name)
    return directory


class SocketHelpersTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="run-weston-comp-"))
        self.runtime = self.tmp / "runtime"
        self.runtime.mkdir()
        self.proc_root = self.tmp / "proc"
        self.proc_root.mkdir()
        self.counter = 0

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def name(self, prefix: str) -> str:
        self.counter += 1
        return f"{prefix}-{self.counter}"

    def listen(self, name: str) -> socket.socket:
        path = self.runtime / name
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(str(path))
        sock.listen(4)
        return sock

    def test_probe_socket(self) -> None:
        sock = self.listen(self.name("wayland-probe"))
        self.assertTrue(cm.probe_unix_socket(sock.getsockname()))
        sock.close()
        self.assertFalse(cm.probe_unix_socket(self.runtime / "fehlt"))

    def test_socket_name_from_cmdline(self) -> None:
        self.assertEqual(
            cm.socket_name_from_cmdline(("weston", "--socket=wayland-weston")),
            "wayland-weston",
        )
        self.assertEqual(
            cm.socket_name_from_cmdline(("weston", "--socket", "wayland-9")), "wayland-9"
        )
        self.assertIsNone(cm.socket_name_from_cmdline(("weston",)))

    def test_command_matches_direct_and_script_style(self) -> None:
        self.assertTrue(cm.command_matches(("weston",), "weston"))
        self.assertTrue(cm.command_matches(("/usr/bin/weston", "--x"), "weston"))
        self.assertTrue(
            cm.command_matches(("/nix/store/python3", "/tmp/bin/fakeweston", "--x"), "fakeweston")
        )
        self.assertFalse(cm.command_matches(("niri",), "weston"))

    def test_replace_socket_arg(self) -> None:
        self.assertEqual(
            cm.replace_socket_arg(("weston", "--socket=alt", "--backend=x11"), "alt", "neu"),
            ("weston", "--socket=neu", "--backend=x11"),
        )
        self.assertEqual(
            cm.replace_socket_arg(("weston", "--socket", "alt"), "alt", "neu"),
            ("weston", "--socket", "neu"),
        )

    def test_unique_socket_name_avoids_collision(self) -> None:
        base = self.name("wayland-weston")
        (self.runtime / base).touch()
        replacement = cm.unique_socket_name(base, self.runtime)
        self.assertEqual(replacement, f"{base}-2")
        (self.runtime / f"{base}-2").touch()
        self.assertEqual(cm.unique_socket_name(base, self.runtime), f"{base}-3")

    def test_candidates_prefer_display_sockets(self) -> None:
        display = self.listen(self.name("wayland-5"))
        control = self.listen(f"niri.{display.getsockname().split('/')[-1]}.99.sock")
        (self.runtime / "wayland-5.lock").touch()
        names = [path.name for path in cm.runtime_socket_candidates(self.runtime)]
        self.assertEqual(names[0], Path(display.getsockname()).name)
        self.assertIn(Path(control.getsockname()).name, names)
        self.assertNotIn("wayland-5.lock", names)

    def test_discover_socket_for_pid_uses_open_files(self) -> None:
        display = self.listen(self.name("wayland-fd"))
        control = self.listen("niri." + Path(display.getsockname()).name + ".42.sock")
        pid = 4242
        fake_process(
            self.proc_root,
            pid,
            [f"/tmp/bin/{self.name('fakecompositor')}"],
            {"3": "socket:[9001]", "4": "socket:[9002]", "5": "pipe:[123]"},
        )
        table = {
            9001: str(control.getsockname()),
            9002: str(display.getsockname()),
            9003: "/somewhere/else/wayland-9",
        }
        self.assertEqual(
            cm.discover_socket_for_pid(
                pid, self.runtime, self.proc_root, unix_paths=table
            ),
            Path(display.getsockname()).name,
        )

    def test_unix_socket_paths_parses_proc_table(self) -> None:
        table_file = self.tmp / "unix"
        table_file.write_text(
            "Num       RefCount Protocol Flags    Type St Inode Path\n"
            "0000: 00000002 00000000 00010000 0001 01 12345 /run/user/1000/wayland-1\n"
            "0000: 00000002 00000000 00010000 0001 01 12346\n",
            encoding="utf-8",
        )
        table = cm.read_unix_socket_paths(table_file)
        self.assertEqual(table, {12345: "/run/user/1000/wayland-1"})

    def test_find_running_instances_reads_cmdline(self) -> None:
        executable = self.name("fakecompositor")
        socket_name = self.name("wayland-inst")
        fake_process(
            self.proc_root,
            5151,
            [executable, "--backend=x11", f"--socket={socket_name}"],
        )
        instances = cm.find_running_instances(
            executable, self.runtime, proc_root=self.proc_root, own_pid=-1
        )
        self.assertEqual(len(instances), 1)
        self.assertEqual(instances[0].pid, 5151)
        self.assertEqual(instances[0].socket, socket_name)

    def test_wayland_display_of_reads_proc_environ(self) -> None:
        pid = 8181
        directory = fake_process(self.proc_root, pid, [self.name("fakecompositor")])
        (directory / "environ").write_bytes(
            b"PATH=/bin\0WAYLAND_DISPLAY=wayland-7\0DISPLAY=:0\0"
        )
        self.assertEqual(cm.wayland_display_of(pid, self.proc_root), "wayland-7")
        self.assertIsNone(cm.wayland_display_of(9999, self.proc_root))

    def test_find_usable_instance_prefers_matching_session(self) -> None:
        executable = self.name("fakecompositor")
        old_socket = self.name("wayland-old")
        new_socket = self.name("wayland-new")
        old_dir = fake_process(self.proc_root, 9191, [executable, f"--socket={old_socket}"])
        fake_process(self.proc_root, 9292, [executable, f"--socket={new_socket}"])
        (old_dir / "environ").write_bytes(b"WAYLAND_DISPLAY=:0\0")
        old = self.listen(old_socket)
        new = self.listen(new_socket)
        try:
            # Ohne Praeferenz gewinnt die neueste Instanz (hoechste PID).
            default = cm.find_usable_instance(
                executable, self.runtime, proc_root=self.proc_root, own_pid=-1
            )
            self.assertEqual(default.pid, 9292)
            # Ein laufendes Kind, das auf dem alten Socket haengt, gewinnt.
            preferred = cm.find_usable_instance(
                executable,
                self.runtime,
                proc_root=self.proc_root,
                own_pid=-1,
                prefer_sockets=(old_socket,),
            )
            self.assertEqual(preferred.pid, 9191)
        finally:
            old.close()
            new.close()

    def test_find_usable_instance_requires_connectable_socket(self) -> None:
        executable = self.name("fakecompositor")
        socket_name = self.name("wayland-usable")
        fake_process(self.proc_root, 6161, [executable, f"--socket={socket_name}"])
        self.assertIsNone(
            cm.find_usable_instance(
                executable, self.runtime, proc_root=self.proc_root, own_pid=-1
            )
        )
        sock = self.listen(socket_name)
        usable = cm.find_usable_instance(
            executable, self.runtime, proc_root=self.proc_root, own_pid=-1
        )
        self.assertIsNotNone(usable)
        self.assertEqual(usable.pid, 6161)
        self.assertEqual(usable.socket, socket_name)
        sock.close()

    def test_foreign_uid_is_ignored(self) -> None:
        executable = self.name("fakecompositor")
        fake_process(self.proc_root, 7171, [executable])
        instances = cm.find_running_instances(
            executable, self.runtime, proc_root=self.proc_root, uid=os.getuid() + 1, own_pid=-1
        )
        self.assertEqual(instances, [])


if __name__ == "__main__":
    unittest.main()
