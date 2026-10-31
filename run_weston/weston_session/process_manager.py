"""Prozesse mit eigener Prozessgruppe, Status, Logs und Bereitschaftspruefung."""

from __future__ import annotations

import os
import re
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from .compositor_manager import discover_socket_for_pid, probe_unix_socket
from .config import Entry, Settings
from .logger import read_from, safe_name, tail_lines

STARTING = "STARTING"
RUNNING = "RUNNING"
READY = "READY"
FAILED = "FAILED"
STOPPING = "STOPPING"
STOPPED = "STOPPED"

ACTIVE_STATES = frozenset({STARTING, RUNNING, READY, STOPPING})
TERMINAL_STATES = frozenset({FAILED, STOPPED})

STARTUP_GRACE = 0.25


def read_start_ticks(pid: int | None, proc_root: str | Path = "/proc") -> int | None:
    """Startzeit aus /proc/<pid>/stat (Feld 22) gegen PID-Recycling."""
    if not pid:
        return None
    try:
        raw = Path(proc_root, str(pid), "stat").read_text(
            encoding="utf-8", errors="replace"
        )
    except OSError:
        return None
    _, _, rest = raw.rpartition(")")
    fields = rest.split()
    if len(fields) < 20:
        return None
    try:
        return int(fields[19])
    except ValueError:
        return None


def pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def process_identity_matches(pid: int | None, ticks: int | None) -> bool:
    if not pid_alive(pid):
        return False
    if ticks is None:
        return True
    return read_start_ticks(pid) == ticks


def extract_socket(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    if not match:
        return None
    groups = match.groupdict()
    value = groups.get("socket")
    if value is None and match.groups():
        value = match.group(1)
    if value is None:
        return None
    value = value.strip().strip("'\"")
    if not value:
        return None
    return value.rsplit("/", 1)[-1]


@dataclass
class ManagedProcess:
    key: str
    name: str
    command: list[str]
    log_path: Path
    requires: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    pid: int | None = None
    pgid: int | None = None
    start_ticks: int | None = None
    started_at: float = field(default_factory=time.time)
    status: str = STARTING
    exit_code: int | None = None
    socket: str | None = None
    message: str = ""
    external: bool = False
    popen: subprocess.Popen | None = field(default=None, repr=False)

    def alive(self) -> bool:
        if self.external:
            return process_identity_matches(self.pid, self.start_ticks)
        if self.popen is None:
            return False
        return self.popen.poll() is None

    @property
    def wedged(self) -> bool:
        return self.status == FAILED and self.alive()

    def describe(self) -> str:
        suffix = f" ({self.message})" if self.message else ""
        return f"{self.name} {self.status}{suffix}"


class ProcessManager:
    """Startet und ueberwacht ausschliesslich eigene Prozesse.

    Fremde Prozesse werden nie beendet; extern wiederverwendete Instanzen sind
    als `external` markiert und werden auch von stop() abgelehnt.
    """

    def __init__(
        self,
        log_dir: str | Path,
        kill_timeout: float = 5.0,
        proc_root: str | Path = "/proc",
        popen_factory: Callable[..., subprocess.Popen] = subprocess.Popen,
    ) -> None:
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.kill_timeout = kill_timeout
        self.proc_root = Path(proc_root)
        self._popen_factory = popen_factory
        self._procs: dict[str, ManagedProcess] = {}
        self._order: list[str] = []
        self._log_offsets: dict[str, int] = {}
        self._lock = threading.RLock()

    # -- Zugriff ---------------------------------------------------------

    def rows(self) -> list[ManagedProcess]:
        with self._lock:
            return [self._procs[key] for key in self._order if key in self._procs]

    def get(self, key: str) -> ManagedProcess | None:
        with self._lock:
            return self._procs.get(key)

    def active(self, name: str) -> ManagedProcess | None:
        proc = self.get(name)
        if proc is None:
            return None
        if proc.status in ACTIVE_STATES or (proc.status == FAILED and proc.alive()):
            return proc
        return None

    def log_path_for(self, name: str) -> Path:
        return self.log_dir / f"{safe_name(name)}.log"

    # -- Start -----------------------------------------------------------

    def start(self, entry: Entry, env: dict[str, str]) -> ManagedProcess:
        with self._lock:
            running = self.active(entry.name)
            if running is not None:
                raise RuntimeError(
                    f"{entry.name} laeuft bereits (PID {running.pid}, {running.status})"
                )
            log_path = self.log_path_for(entry.name)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                handle = open(log_path, "wb")
            except OSError as exc:
                raise RuntimeError(f"Logdatei {log_path} nicht schreibbar: {exc}") from exc
            try:
                handle.write(
                    (
                        f"# run_weston: {entry.name}\n# Befehl: {entry.command}\n"
                        f"# XDG_RUNTIME_DIR={env.get('XDG_RUNTIME_DIR', '')}\n"
                        f"# WAYLAND_DISPLAY={env.get('WAYLAND_DISPLAY', '')}\n"
                    ).encode()
                )
                handle.flush()
                try:
                    popen = self._popen_factory(
                        list(entry.command),
                        env=env,
                        stdin=subprocess.DEVNULL,
                        stdout=handle,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                        close_fds=True,
                    )
                except (OSError, ValueError) as exc:
                    raise RuntimeError(
                        f"{entry.name} konnte nicht gestartet werden: {exc}"
                    ) from exc
            finally:
                handle.close()

            pgid = popen.pid
            try:
                pgid = os.getpgid(popen.pid)
            except OSError:
                pass
            proc = ManagedProcess(
                key=entry.name,
                name=entry.name,
                command=list(entry.command),
                log_path=log_path,
                requires=list(entry.requires),
                env=dict(env),
                pid=popen.pid,
                pgid=pgid,
                start_ticks=read_start_ticks(popen.pid, self.proc_root),
                started_at=time.time(),
                status=STARTING,
                popen=popen,
                message=f"PID {popen.pid}",
            )
            self._procs[entry.name] = proc
            if entry.name not in self._order:
                self._order.append(entry.name)
            self._log_offsets[entry.name] = 0
            return proc

    def register_external(
        self, entry: Entry, pid: int | None, socket_name: str | None
    ) -> ManagedProcess:
        """Bereits laufende Instanz uebernehmen (wird nie beendet)."""
        with self._lock:
            proc = ManagedProcess(
                key=entry.name,
                name=entry.name,
                command=list(entry.command),
                log_path=self.log_path_for(entry.name),
                requires=list(entry.requires),
                pid=pid,
                start_ticks=read_start_ticks(pid, self.proc_root),
                status=READY,
                socket=socket_name,
                external=True,
                message="laufende Instanz uebernommen",
            )
            self._procs[entry.name] = proc
            if entry.name not in self._order:
                self._order.append(entry.name)
            return proc

    def adopt(self, entry: Entry, pid: int, ticks: int | None, socket_name: str | None) -> ManagedProcess:
        """Prozess aus einem frueheren Manager-Lauf uebernehmen (nicht beenden)."""
        proc = self.register_external(entry, pid, socket_name)
        proc.start_ticks = ticks
        proc.message = "aus vorherigem Lauf uebernommen"
        return proc

    # -- Status ----------------------------------------------------------

    def mark_running(self, key: str) -> None:
        with self._lock:
            proc = self._procs.get(key)
            if proc is not None and proc.status == STARTING:
                proc.status = RUNNING

    def mark_ready(self, key: str, socket_name: str | None = None) -> None:
        with self._lock:
            proc = self._procs.get(key)
            if proc is None:
                return
            proc.status = READY
            if socket_name:
                proc.socket = socket_name
            proc.message = f"PID {proc.pid}" if proc.pid else "bereit"

    def mark_failed(
        self, key: str, message: str, exit_code: int | None = None
    ) -> None:
        with self._lock:
            proc = self._procs.get(key)
            if proc is None:
                return
            if proc.status == STOPPED:
                return
            proc.status = FAILED
            proc.message = message
            if exit_code is not None:
                proc.exit_code = exit_code

    def poll(self) -> None:
        """Status anhand der Prozessliste aktualisieren (nicht blockierend)."""
        with self._lock:
            for proc in self._procs.values():
                if proc.status not in (STARTING, RUNNING, READY):
                    continue
                if proc.external:
                    if not proc.alive():
                        proc.status = STOPPED
                        proc.message = "Instanz ist beendet"
                    continue
                if proc.popen is None:
                    continue
                code = proc.popen.poll()
                if code is None:
                    if proc.status == STARTING:
                        proc.status = RUNNING
                    continue
                proc.exit_code = code
                proc.status = FAILED
                proc.message = f"Prozess beendet (Exit {code})"

    # -- Bereitschaft ----------------------------------------------------

    def wait_ready(
        self,
        proc: ManagedProcess,
        entry: Entry,
        settings: Settings,
        runtime_dir: str | Path,
        pattern: re.Pattern[str] | None = None,
        probe: Callable[[str | Path | None], bool] = probe_unix_socket,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> bool:
        """Blockierender Ready-Check; laeuft immer in einem Worker-Thread."""
        timeout = entry.timeout(settings)
        deadline = clock() + timeout
        if pattern is None and entry.ready_pattern:
            pattern = re.compile(entry.ready_pattern)

        if entry.ready_check == "process":
            sleeper(min(STARTUP_GRACE, max(0.01, timeout / 4)))
            return self._ready_or_dead(proc)

        while clock() < deadline:
            if entry.ready_check == "exit":
                code = proc.popen.poll() if proc.popen else None
                if code is not None:
                    if code == 0:
                        self.mark_ready(proc.key)
                        proc.exit_code = 0
                        proc.message = "erfolgreich beendet (Exit 0)"
                        return True
                    self.mark_failed(proc.key, f"Exit {code}", code)
                    return False
            else:
                if proc.popen is not None and proc.popen.poll() is not None:
                    code = proc.popen.poll()
                    self.mark_failed(proc.key, f"Prozess beendet (Exit {code})", code)
                    return False

                if entry.ready_check == "socket":
                    candidate = (
                        proc.socket
                        or entry.socket
                        or discover_socket_for_pid(proc.pid or 0, runtime_dir, self.proc_root)
                    )
                    if candidate and probe(Path(runtime_dir) / candidate):
                        self.mark_ready(proc.key, candidate)
                        return True
                    if candidate and proc.socket is None:
                        proc.socket = candidate

                elif entry.ready_check == "log":
                    if pattern is not None:
                        text, offset = read_from(
                            proc.log_path, self._log_offsets.get(proc.key, 0)
                        )
                        self._log_offsets[proc.key] = offset
                        socket_name = extract_socket(pattern, text) if text else None
                        if socket_name:
                            proc.socket = socket_name
                            if entry.socket_detection in ("none",):
                                self.mark_ready(proc.key, socket_name)
                                return True
                            if probe(Path(runtime_dir) / socket_name):
                                self.mark_ready(proc.key, socket_name)
                                return True
                    # Ohne Logmuster notfalls ueber die offenen Sockets gehen.
                    if pattern is None:
                        socket_name = discover_socket_for_pid(
                            proc.pid or 0, runtime_dir, self.proc_root
                        )
                        if socket_name:
                            self.mark_ready(proc.key, socket_name)
                            return True

                elif entry.ready_check == "command":
                    if self._ready_command_ok(proc, entry, settings):
                        self.mark_ready(proc.key, proc.socket)
                        return True

            sleeper(0.1)

        self.mark_failed(
            proc.key, f"Timeout nach {timeout:g}s (ready_check={entry.ready_check})"
        )
        return False

    def _ready_or_dead(self, proc: ManagedProcess) -> bool:
        if proc.popen is not None and proc.popen.poll() is not None:
            code = proc.popen.poll()
            self.mark_failed(proc.key, f"sofort beendet (Exit {code})", code)
            return False
        self.mark_ready(proc.key)
        return True

    def _ready_command_ok(
        self, proc: ManagedProcess, entry: Entry, settings: Settings
    ) -> bool:
        if not entry.ready_command:
            return True
        try:
            result = subprocess.run(
                list(entry.ready_command),
                env=proc.env or None,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=min(5.0, entry.timeout(settings)),
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return result.returncode == 0

    # -- Beenden ---------------------------------------------------------

    def _signal(self, proc: ManagedProcess, sig: int) -> None:
        if proc.external or proc.pid is None:
            return
        if not process_identity_matches(proc.pid, proc.start_ticks):
            return
        try:
            if proc.pgid:
                os.killpg(proc.pgid, sig)
            else:
                os.kill(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            try:
                os.kill(proc.pid, sig)
            except (ProcessLookupError, PermissionError):
                pass

    def stop(self, key: str, timeout: float | None = None) -> bool:
        proc = self.get(key)
        if proc is None:
            return False
        if proc.external:
            raise RuntimeError(
                f"{proc.name} wurde nicht vom Manager gestartet und wird nicht beendet"
            )
        with self._lock:
            if proc.popen is None:
                proc.status = STOPPED
                proc.message = "kein Prozesshandle"
                return True
            if proc.popen.poll() is not None:
                proc.exit_code = proc.popen.returncode
                proc.status = STOPPED
                proc.message = f"bereits beendet (Exit {proc.exit_code})"
                return True
            proc.status = STOPPING
            proc.message = "SIGTERM"

        self._signal(proc, signal.SIGTERM)
        wait = self.kill_timeout if timeout is None else timeout
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            if proc.popen.poll() is not None:
                break
            time.sleep(0.05)

        if proc.popen.poll() is None:
            self._signal(proc, signal.SIGKILL)
            try:
                proc.popen.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass

        with self._lock:
            proc.exit_code = proc.popen.poll()
            proc.status = STOPPED
            proc.message = (
                "beendet"
                if proc.exit_code is None
                else f"beendet (Exit {proc.exit_code})"
            )
        return True

    def stop_all(self, names: Iterable[str] | None = None) -> list[str]:
        wanted = set(names) if names is not None else None
        stopped: list[str] = []
        for proc in reversed(self.rows()):
            if wanted is not None and proc.name not in wanted:
                continue
            if proc.external:
                continue
            try:
                if self.stop(proc.key):
                    stopped.append(proc.name)
            except RuntimeError:
                continue
        return stopped

    # -- Logs ------------------------------------------------------------

    def tail(self, key: str, count: int = 40) -> list[str]:
        proc = self.get(key)
        if proc is None:
            return []
        if proc.external:
            return [f"{proc.name}: externe Instanz (PID {proc.pid}), kein Manager-Log"]
        return tail_lines(proc.log_path, count)
