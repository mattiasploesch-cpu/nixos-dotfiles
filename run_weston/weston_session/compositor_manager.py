"""Erkennung bereits laufender Compositoren und ihrer Wayland-Sockets."""

from __future__ import annotations

import os
import socket
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

SOCKET_PREFIX = "wayland-"


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    uid: int
    command: tuple[str, ...]

    @property
    def executable(self) -> str:
        return Path(self.command[0]).name if self.command else ""


@dataclass(frozen=True)
class ExistingInstance:
    name: str
    pid: int
    socket: str | None
    command: tuple[str, ...]
    executable: str = ""

    @property
    def socket_path(self) -> str | None:
        return self.socket


def iter_processes(proc_root: str | Path = "/proc") -> Iterable[ProcessInfo]:
    """Alle Prozesse aus /proc lesen (nur argv und UID, keine Rechte noetig)."""
    root = Path(proc_root)
    try:
        entries = sorted(root.iterdir(), key=lambda item: item.name)
    except OSError:
        return
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            uid = entry.stat().st_uid
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        parts = [part for part in raw.split(b"\0") if part]
        if not parts:
            continue
        command = tuple(part.decode("utf-8", "replace") for part in parts)
        yield ProcessInfo(pid=int(entry.name), uid=uid, command=command)


def probe_unix_socket(path: str | Path | None, timeout: float = 0.25) -> bool:
    """Kann eine Verbindung zu diesem Unix-Socket aufgebaut werden?"""
    if not path:
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(str(path))
        return True
    except (OSError, ValueError):
        return False


def runtime_socket_candidates(runtime_dir: str | Path) -> list[Path]:
    """Sockets im Runtime-Verzeichnis, Wayland-Displays zuerst.

    Niri legt zusaetzlich einen IPC-Socket (niri.wayland-<n>.<pid>.sock) an.
    Der ist kein Display-Socket und wird deshalb nach hinten sortiert.
    """
    root = Path(runtime_dir)
    try:
        entries = list(root.iterdir())
    except OSError:
        return []

    def score(path: Path) -> tuple[int, str]:
        name = path.name
        try:
            mode = path.stat().st_mode
        except OSError:
            return (3, name)
        if not stat.S_ISSOCK(mode):
            return (3, name)
        if name.startswith(SOCKET_PREFIX) and name.count(".") == 0:
            return (0, name)
        if name.startswith(SOCKET_PREFIX):
            return (1, name)
        if name.endswith(".sock"):
            return (2, name)
        return (2, name)

    candidates = [path for path in entries if score(path)[0] < 3]
    return sorted(candidates, key=score)


def read_unix_socket_paths(
    table_path: str | Path = "/proc/net/unix",
) -> dict[int, str]:
    """Inode -> Pfad fuer gebundene Unix-Sockets aus /proc/net/unix.

    `/proc/<pid>/fd` zeigt Sockfs-Inodes; `stat()` auf die Socketdatei liefert
    dagegen den Dateisystem-Inode. Beide sind nicht vergleichbar, deshalb wird
    die Zuordnung ueber /proc/net/unix hergestellt.
    """
    table: dict[int, str] = {}
    try:
        content = Path(table_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return table
    for line in content.splitlines()[1:]:
        fields = line.split(None, 7)
        if len(fields) < 8:
            continue
        try:
            inode = int(fields[6])
        except ValueError:
            continue
        path = fields[7].strip()
        if path.startswith("/"):
            table[inode] = path
    return table


def discover_socket_for_pid(
    pid: int,
    runtime_dir: str | Path,
    proc_root: str | Path = "/proc",
    unix_paths: dict[int, str] | None = None,
) -> str | None:
    """Findet den Wayland-Socket eines Prozesses ueber die offenen Dateien.

    Das ist unabhaengig von Logtexten und funktioniert fuer jedes Programm,
    das seinen Socket im XDG_RUNTIME_DIR oeffnet. Bei mehreren Sockets werden
    Display-Sockets (wayland-N) gegenueber IPC-Sockets (niri.wayland-*.sock)
    bevorzugt.
    """
    table = read_unix_socket_paths() if unix_paths is None else unix_paths
    if not table:
        return None
    runtime = Path(runtime_dir)
    try:
        fd_entries = list(Path(proc_root, str(pid), "fd").iterdir())
    except OSError:
        return None

    found: list[str] = []
    for fd in fd_entries:
        try:
            target = os.readlink(fd)
        except OSError:
            continue
        if not (target.startswith("socket:[") and target.endswith("]")):
            continue
        try:
            inode = int(target[len("socket:[") : -1])
        except ValueError:
            continue
        path = table.get(inode)
        if not path:
            continue
        candidate = Path(path)
        try:
            same_dir = candidate.parent.resolve() == runtime.resolve()
        except OSError:
            same_dir = candidate.parent == runtime
        if not same_dir:
            continue
        if candidate.name not in found:
            found.append(candidate.name)
    if not found:
        return None
    found.sort(
        key=lambda name: (
            0 if name.startswith(SOCKET_PREFIX) and name.count(".") == 0 else 1,
            name,
        )
    )
    return found[0]


def replace_socket_arg(
    command: Sequence[str], old: str, new: str
) -> tuple[str, ...]:
    """Ersetzt --socket=<old> bzw. --socket <old> in einer Argumentliste."""
    result: list[str] = []
    skip_next = False
    for index, arg in enumerate(command):
        if skip_next:
            skip_next = False
            result.append(new if arg == old else arg)
            continue
        if arg == old:
            result.append(new)
        elif arg == f"--socket={old}":
            result.append(f"--socket={new}")
        elif arg == "--socket" and index + 1 < len(command):
            result.append(arg)
            skip_next = True
        else:
            result.append(arg)
    return tuple(result)


def command_matches(command: Sequence[str], executable: str) -> bool:
    """Gehoert diese /proc-Kommandozeile zu `executable`?

    Skripte mit Shebang laufen als "python3 /pfad/skript ...", deshalb werden
    auch die ersten Argumente geprueft und nicht nur argv[0].
    """
    wanted = Path(executable).name
    if not command:
        return False
    if Path(command[0]).name == wanted:
        return True
    return any(Path(arg).name == wanted for arg in list(command)[1:3])


def socket_name_from_cmdline(command: tuple[str, ...]) -> str | None:
    for index, arg in enumerate(command):
        if arg.startswith("--socket="):
            return arg.split("=", 1)[1] or None
        if arg == "--socket" and index + 1 < len(command):
            return command[index + 1] or None
    return None


def unique_socket_name(base: str, runtime_dir: str | Path) -> str:
    """Verhindert Kollisionen mit vorhandenen Sockets beim Neustart."""
    root = Path(runtime_dir)
    candidate = base
    counter = 2
    while (root / candidate).exists() or (root / f"{candidate}.lock").exists():
        candidate = f"{base}-{counter}"
        counter += 1
    return candidate


def find_running_instances(
    executable: str,
    runtime_dir: str | Path,
    proc_root: str | Path = "/proc",
    uid: int | None = None,
    own_pid: int | None = None,
) -> list[ExistingInstance]:
    """Alle Prozesse des Benutzers mit diesem Programmnamen samt Socket."""
    wanted = Path(executable).name
    wanted_uid = os.getuid() if uid is None else uid
    skip_pid = os.getpid() if own_pid is None else own_pid
    found: list[ExistingInstance] = []
    for info in iter_processes(proc_root):
        if info.uid != wanted_uid or info.pid == skip_pid:
            continue
        if not command_matches(info.command, executable):
            continue
        socket_name = socket_name_from_cmdline(info.command)
        if socket_name is None:
            socket_name = discover_socket_for_pid(info.pid, runtime_dir, proc_root)
        found.append(
            ExistingInstance(
                name=info.executable,
                pid=info.pid,
                socket=socket_name,
                command=info.command,
                executable=info.executable,
            )
        )
    # Neueste Instanz zuerst: bei mehreren Laeufen ist die aktuelle Sitzung
    # die zuletzt gestartete.
    found.sort(key=lambda instance: instance.pid, reverse=True)
    return found


def find_usable_instance(
    executable: str,
    runtime_dir: str | Path,
    socket_names: Iterable[str | None] = (),
    proc_root: str | Path = "/proc",
    uid: int | None = None,
    own_pid: int | None = None,
) -> ExistingInstance | None:
    """Erste laufende Instanz, deren Socket tatsaechlich verbindbar ist.

    Geprueft werden nur der ermittelte Socket des Prozesses und explizit
    erwartete Namen - niemals beliebige Sockets aus dem Runtime-Verzeichnis.
    """
    known = {name for name in socket_names if name}
    for instance in find_running_instances(
        executable, runtime_dir, proc_root=proc_root, uid=uid, own_pid=own_pid
    ):
        candidates: list[str] = []
        if instance.socket:
            candidates.append(instance.socket)
        candidates.extend(sorted(known))
        if not candidates:
            continue
        for candidate in candidates:
            path = Path(runtime_dir) / candidate
            if probe_unix_socket(path):
                return ExistingInstance(
                    name=instance.name,
                    pid=instance.pid,
                    socket=candidate,
                    command=instance.command,
                    executable=instance.executable,
                )
    return None
