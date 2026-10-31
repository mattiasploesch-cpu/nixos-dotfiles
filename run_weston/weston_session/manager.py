"""Orchestrierung: Abhaengigkeiten, Start, Status und TUI-Kommandos."""

from __future__ import annotations

import dataclasses
import json
import os
import shlex
import threading
import time
import traceback
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from . import compositor_manager as compositors
from . import process_manager as pm
from .config import Config, ConfigError, Entry, Settings, load_config
from .diagnostics import Diagnostic, run_doctor
from .environment_manager import EnvironmentManager
from .logger import SessionLogger
from .paths import Paths
from .process_manager import ManagedProcess, ProcessManager

HELP_LINES = (
    "Kommandos:",
    "  <programm> [args]     bekanntes Programm starten (inkl. Abhaengigkeiten)",
    "  run --env <name> cmd  beliebigen Befehl in der Umgebung von <name> starten",
    "  k <nummer>            Prozess kontrolliert beenden (SIGTERM, dann SIGKILL)",
    "  logs [nummer|name]    letzte Logzeilen anzeigen (ohne Argument: Manager-Log)",
    "  status                Konfiguration und Prozessstatus",
    "  doctor                Systempruefung (Weston, Niri, Sockets, Rechte ...)",
    "  reload                Konfiguration neu laden",
    "  refresh               Status sofort aktualisieren",
    "  clear                 Ausgabe leeren",
    "  help                  diese Hilfe",
    "  exit                  Manager beenden (verwaltete Prozesse werden beendet)",
)


@dataclass(frozen=True)
class Message:
    level: str  # info | ok | warn | error | cmd
    text: str
    stamp: float = field(default_factory=time.time)


class SessionManager:
    """Haelt den Zustand einer Manager-Sitzung.

    Starts laufen immer in Worker-Threads, damit die TUI bedienbar bleibt.
    Der Manager beendet ausschliesslich Prozesse, die er selbst gestartet hat.
    """

    def __init__(
        self,
        paths: Paths | None = None,
        config: Config | None = None,
        config_path: str | os.PathLike[str] | None = None,
        process_manager: ProcessManager | None = None,
        env_manager: EnvironmentManager | None = None,
        logger: SessionLogger | None = None,
        base_env: dict[str, str] | None = None,
        proc_root: str | Path = "/proc",
    ) -> None:
        self.paths = (paths or Paths.from_env(config=config_path)).ensure()
        self.logger = logger or SessionLogger(self.paths.logs)
        self.proc_root = Path(proc_root)
        self.config = config or load_config(self.paths.config)
        self.procs = process_manager or ProcessManager(
            self.paths.logs, self.config.settings.kill_timeout, proc_root=self.proc_root
        )
        self.env = env_manager or EnvironmentManager(
            self.paths.runtime, base_env=base_env
        )
        self.messages: deque[Message] = deque(maxlen=4000)
        self.exit_requested = False
        self.last_doctor: list[Diagnostic] = []
        self._lock = threading.RLock()
        self._inflight: dict[str, threading.Event] = {}
        self._threads: list[threading.Thread] = []
        self._state_path = self.paths.state_dir / "managed.json"

        self.emit("info", f"run_weston bereit - Konfiguration: {self.paths.config}")
        for warning in self.config.warnings:
            self.emit("warn", warning)
        self._adopt_leftovers()

    # -- Basiszugriff ----------------------------------------------------

    @property
    def settings(self) -> Settings:
        return self.config.settings

    def emit(self, level: str, text: str) -> None:
        with self._lock:
            self.messages.append(Message(level, text))
        if level == "error":
            self.logger.error(text)
        elif level == "warn":
            self.logger.warn(text)
        else:
            self.logger.info(text)

    def snapshot_messages(self) -> list[Message]:
        with self._lock:
            return list(self.messages)

    def clear_messages(self) -> None:
        with self._lock:
            self.messages.clear()

    def rows(self) -> list[ManagedProcess]:
        return self.procs.rows()

    def tick(self) -> None:
        self.procs.poll()

    def active_display(self) -> str | None:
        """Display, auf dem neue Programme landen: die tiefste READY-Ebene.

        Nicht einfach der zuletzt gestartete Prozess: nach dem Start von dms
        bleibt der Socket von Niri der aktive, nicht der von Weston.
        """
        entries = self.config.entries()
        best: tuple[int, str] | None = None
        for proc in self.procs.rows():
            if proc.status != pm.READY or not proc.socket:
                continue
            depth = self._depth(entries, proc.name)
            if best is None or depth >= best[0]:
                best = (depth, proc.socket)
        return best[1] if best else None

    def _depth(self, entries: dict[str, Entry], name: str) -> int:
        entry = entries.get(name)
        if entry is None or not entry.requires:
            return 0
        return 1 + max(self._depth(entries, dependency) for dependency in entry.requires)

    def completion_candidates(self) -> list[str]:
        base = [
            "help",
            "status",
            "doctor",
            "reload",
            "refresh",
            "clear",
            "logs",
            "k",
            "run",
            "exit",
        ]
        return sorted(set(base) | set(self.config.names()))

    # -- Start -----------------------------------------------------------

    def start(self, name: str, extra_args: tuple[str, ...] = ()) -> bool:
        """Startet einen Eintrag non-blocking; liefert False bei unbekanntem Namen."""
        entry = self.config.entry(name)
        if entry is None:
            self.emit("error", f"Unbekanntes Programm: {name!r} (siehe 'help')")
            return False
        target = self._with_args(entry, extra_args)
        thread = threading.Thread(
            target=self._worker,
            args=(target,),
            name=f"run-weston-{name}",
            daemon=True,
        )
        with self._lock:
            self._threads = [item for item in self._threads if item.is_alive()]
            self._threads.append(thread)
        thread.start()
        return True

    def _with_args(self, entry: Entry, extra_args: tuple[str, ...]) -> Entry:
        if not extra_args:
            return entry
        base = list(entry.command)
        requested = list(extra_args)
        if requested and base[1:] == requested:
            return entry
        merged = base + requested
        return dataclasses.replace(entry, command=tuple(merged))

    def _worker(self, entry: Entry) -> None:
        try:
            self._ensure_entry(entry)
        except Exception as exc:  # noqa: BLE001 - TUI darf nie abstuerzen
            self.emit("error", f"Unerwarteter Fehler bei {entry.name}: {exc}")
            self.logger.error(traceback.format_exc())

    def _ensure_ready(self, name: str, stack: tuple[str, ...] = ()) -> bool:
        entry = self.config.entry(name)
        if entry is None:
            self.emit("error", f"Abhaengigkeit {name!r} ist nicht definiert")
            return False
        return self._ensure_entry(entry, stack)

    def _ensure_entry(self, entry: Entry, stack: tuple[str, ...] = ()) -> bool:
        if entry.name in stack:
            self.emit(
                "error",
                f"Zyklische Abhaengigkeit: {' -> '.join([*stack, entry.name])}",
            )
            return False

        for dependency in entry.requires:
            if not self._ensure_ready(dependency, (*stack, entry.name)):
                self.emit(
                    "error",
                    f"{entry.name} wird nicht gestartet: "
                    f"Abhaengigkeit {dependency!r} ist nicht bereit",
                )
                return False

        event = self._claim_start(entry)
        if event is None:
            # Anderer Thread startet bereits oder der Eintrag ist schon bereit.
            return self._wait_for_status(entry)

        try:
            return self._launch(entry)
        finally:
            with self._lock:
                self._inflight.pop(entry.name, None)
            event.set()

    def _claim_start(self, entry: Entry) -> threading.Event | None:
        deadline = time.monotonic() + entry.timeout(self.settings) + 10
        while True:
            with self._lock:
                proc = self.procs.get(entry.name)
                status = proc.status if proc else None
                if status == pm.READY:
                    return None
                if status in (pm.STARTING, pm.RUNNING):
                    event = self._inflight.get(entry.name)
                elif proc is not None and proc.alive() and status in (pm.FAILED, pm.STOPPED):
                    self.emit(
                        "error",
                        f"{entry.name} laeuft noch im Status {status} "
                        f"({proc.message}) - bitte vorher beenden ('k ...')",
                    )
                    return None
                else:
                    event = threading.Event()
                    self._inflight[entry.name] = event
                    return event
            if time.monotonic() > deadline:
                self.emit("warn", f"{entry.name}: warte zu lange auf laufenden Start")
                return None
            if event is not None:
                event.wait(0.1)
            else:
                time.sleep(0.1)

    def _wait_for_status(self, entry: Entry) -> bool:
        proc = self.procs.get(entry.name)
        return proc is not None and proc.status == pm.READY

    def _effective_entry(self, entry: Entry) -> Entry:
        """Weicht auf einen freien Socketnamen aus, wenn der alte belegt ist."""
        if not entry.is_compositor or not entry.socket:
            return entry
        path = self.paths.runtime / entry.socket
        if not path.exists() or compositors.probe_unix_socket(path):
            return entry
        replacement = compositors.unique_socket_name(entry.socket, self.paths.runtime)
        command = compositors.replace_socket_arg(
            entry.command, entry.socket, replacement
        )
        self.emit(
            "warn",
            f"{entry.name}: Socket {entry.socket} ist belegt, aber nicht erreichbar - "
            f"starte mit {replacement}",
        )
        return dataclasses.replace(entry, socket=replacement, command=command)

    def _parent_display(self, entry: Entry) -> str | None:
        display = None
        for dependency in entry.requires:
            dep_proc = self.procs.get(dependency)
            if dep_proc is not None and dep_proc.socket:
                display = dep_proc.socket
        return display

    def _is_reusable(self, entry: Entry) -> bool:
        """Compositoren und socket-faehige Programme werden wiederverwendet.

        Niri steht in der Regel unter [programs.niri]; entscheidend ist, dass
        der Eintrag einen Wayland-Socket besitzt (ready_check socket/log).
        Reine Programme wie dms oder foot werden nie "wiederverwendet", damit
        sie sich normal starten lassen.
        """
        if not self.settings.reuse_existing:
            return False
        if entry.is_compositor:
            return True
        return entry.socket is not None or entry.ready_check in ("socket", "log")

    def _child_displays(self, entry: Entry) -> dict[str, str]:
        """Laufende Programme, die bereits auf diesem Compositor haengen.

        Deren WAYLAND_DISPLAY ist der Socket des Compositors, den sie benutzen
        (z. B. Niri -> wayland-weston). So laesst sich die passende Instanz
        waehlen, statt sich auf die neueste PID zu verlassen.
        """
        displays: dict[str, str] = {}
        for child in self.config.entries().values():
            if entry.name not in child.requires:
                continue
            for instance in compositors.find_running_instances(
                child.command[0], self.paths.runtime, proc_root=self.proc_root
            ):
                display = compositors.wayland_display_of(instance.pid, self.proc_root)
                if display:
                    displays[child.name] = display
                    break
        return displays

    def _find_existing(self, entry: Entry) -> compositors.ExistingInstance | None:
        if not self._is_reusable(entry):
            return None
        socket_names = [entry.socket] if entry.socket else []
        prefer_sockets: tuple[str, ...] = ()
        prefer_display = None
        if entry.is_compositor:
            prefer_sockets = tuple(self._child_displays(entry).values())
        else:
            prefer_display = self._parent_display(entry)
        return compositors.find_usable_instance(
            entry.command[0],
            self.paths.runtime,
            socket_names=socket_names,
            proc_root=self.proc_root,
            prefer_sockets=prefer_sockets,
            prefer_wayland_display=prefer_display,
        )

    def _check_display_consistency(self, entry: Entry) -> None:
        """Warnt, wenn ein Kind auf einem anderen Compositor laeuft."""
        if not entry.is_compositor:
            return
        proc = self.procs.get(entry.name)
        if proc is None or not proc.socket:
            return
        for child, display in self._child_displays(entry).items():
            if display != proc.socket:
                self.emit(
                    "warn",
                    f"{child} laeuft auf {display}, {entry.name} auf {proc.socket} - "
                    "das sind getrennte Desktops",
                )

    def _launch(self, entry: Entry) -> bool:
        existing = self._find_existing(entry)
        if existing is not None:
            self.procs.register_external(entry, existing.pid, existing.socket)
            self.emit(
                "ok",
                f"{entry.name}: laufende Instanz wird wiederverwendet "
                f"(PID {existing.pid}, Socket {existing.socket or 'unbekannt'})",
            )
            self._check_display_consistency(entry)
            return True

        effective = self._effective_entry(entry)
        display = self._parent_display(effective)
        env = self.env.for_entry(
            effective.command,
            backend=effective.backend,
            wayland_display=display,
            extra_env=effective.env,
            unset_env=effective.unset_env,
        )
        try:
            proc = self.procs.start(effective, env)
        except RuntimeError as exc:
            self.emit("error", str(exc))
            return False

        self.emit(
            "info",
            f"{entry.name} gestartet (PID {proc.pid}"
            + (f", WAYLAND_DISPLAY={display}" if display else "")
            + ")",
        )
        ok = self.procs.wait_ready(proc, effective, self.settings, self.paths.runtime)
        if ok:
            detail = f", Socket {proc.socket}" if proc.socket else ""
            self.emit("ok", f"{entry.name} ist bereit{detail}")
            self._check_display_consistency(entry)
            self.save_state()
        else:
            self.emit(
                "error",
                f"{entry.name} ist fehlgeschlagen: {proc.message} "
                f"(Log: {proc.log_path})",
            )
            for line in self.procs.tail(proc.key, 8):
                self.emit("error", f"  | {line}")
            self.save_state()
        return ok

    # -- Ad-hoc-Befehle --------------------------------------------------

    def _unique_name(self, base: str) -> str:
        name = base
        counter = 2
        while self.procs.get(name) is not None:
            name = f"{base}#{counter}"
            counter += 1
        return name

    def run_in_environment(
        self, env_name: str, command: tuple[str, ...], ready_check: str
    ) -> bool:
        env_entry = self.config.entry(env_name)
        if env_entry is None:
            self.emit("error", f"Unbekannte Umgebung: {env_name!r}")
            return False
        if not command:
            self.emit("error", "run: kein Befehl angegeben")
            return False
        name = self._unique_name(Path(command[0]).name or "programm")
        adhoc = Entry(
            name=name,
            kind="programs",
            command=tuple(command),
            requires=(env_name,),
            socket_detection="none",
            ready_check=ready_check,
        )
        thread = threading.Thread(
            target=self._worker,
            args=(adhoc,),
            name=f"run-weston-{name}",
            daemon=True,
        )
        with self._lock:
            self._threads = [item for item in self._threads if item.is_alive()]
            self._threads.append(thread)
        thread.start()
        return True

    # -- Kommandos -------------------------------------------------------

    def handle_command(self, text: str) -> None:
        """Verarbeitet eine TUI-Eingabe; kehrt sofort zurueck."""
        stripped = text.strip()
        if not stripped:
            return
        self.emit("cmd", f"> {stripped}")
        try:
            argv = shlex.split(stripped)
        except ValueError as exc:
            self.emit("error", f"Eingabe nicht lesbar: {exc}")
            return
        if not argv:
            return

        command = argv[0]
        rest = argv[1:]

        if command in ("help", "?"):
            for line in HELP_LINES:
                self.emit("info", line)
        elif command in ("exit", "quit"):
            self.exit_requested = True
            self.emit("info", "Beende Manager ...")
        elif command == "status":
            for line in self.status_lines():
                self.emit("info", line)
        elif command == "doctor":
            self.doctor_async()
        elif command == "reload":
            self.reload_config()
        elif command == "refresh":
            self.tick()
            self.emit("info", "Status aktualisiert")
        elif command == "clear":
            self.clear_messages()
            self.emit("info", "Ausgabe geleert")
        elif command in ("logs", "log"):
            self._show_logs(rest)
        elif command in ("k", "kill", "stop"):
            self._kill(rest)
        elif command == "run":
            self._run_command(rest)
        else:
            entry = self.config.entry(command)
            if entry is None:
                self._unknown_command(argv)
            else:
                self.start(command, tuple(rest))

    def _unknown_command(self, argv: list[str]) -> None:
        if self.settings.allow_unknown_programs:
            env_name = self.config.unknown.env or self.settings.default_compositor
            self.run_in_environment(
                env_name, tuple(argv), self.config.unknown.ready_check
            )
            return
        env_name = self.config.unknown.env or self.settings.default_compositor
        self.emit(
            "error",
            f"Unbekanntes Programm {argv[0]!r} wird nicht gestartet "
            "(allow_unknown_programs = false).",
        )
        self.emit(
            "info",
            f"Nutze eine definierte Umgebung: run --env {env_name} {' '.join(argv)}",
        )

    def _run_command(self, args: list[str]) -> None:
        if not args:
            self.emit("error", "Aufruf: run --env <name> <befehl> [args]")
            return
        if args[0] in ("--env", "-e"):
            if len(args) < 3:
                self.emit("error", "Aufruf: run --env <name> <befehl> [args]")
                return
            env_name, command = args[1], args[2:]
        else:
            env_name, command = self.settings.default_compositor, args
        if env_name == "default":
            env_name = self.settings.default_compositor
        known = self.config.entry(command[0])
        if known is not None:
            # Bekannte Programme behalten ihre konfigurierten Regeln.
            self.start(known.name, tuple(command[1:]))
            return
        ready_check = self.config.unknown.ready_check
        self.run_in_environment(env_name, tuple(command), ready_check)

    def _row_by_index(self, number: int) -> ManagedProcess | None:
        rows = self.procs.rows()
        if 1 <= number <= len(rows):
            return rows[number - 1]
        return None

    def _kill(self, args: list[str]) -> None:
        if not args or not args[0].isdigit():
            self.emit("error", "Aufruf: k <nummer> (Nummer aus der Prozessliste)")
            return
        proc = self._row_by_index(int(args[0]))
        if proc is None:
            self.emit("error", f"Kein Prozess mit Nummer {args[0]}")
            return
        if proc.external:
            self.emit(
                "warn",
                f"{proc.name} wurde nicht vom Manager gestartet und wird nicht beendet",
            )
            return

        def worker() -> None:
            try:
                self.procs.stop(proc.key)
                self.emit("ok", f"{proc.name} beendet (Exit {proc.exit_code})")
            except Exception as exc:  # noqa: BLE001
                self.emit("error", f"{proc.name} konnte nicht beendet werden: {exc}")
            self.save_state()

        threading.Thread(target=worker, name=f"stop-{proc.name}", daemon=True).start()

    def _show_logs(self, args: list[str]) -> None:
        if not args:
            for line in self.logger.tail("manager", 30):
                self.emit("info", line)
            return
        target = args[0]
        proc = self._row_by_index(int(target)) if target.isdigit() else None
        if proc is None:
            name_match = self.config.entry(target)
            if name_match is None:
                self.emit("error", f"Kein Prozess und kein Eintrag zu {target!r}")
                return
            proc = self.procs.get(name_match.name)
        if proc is None:
            self.emit("warn", f"{target}: noch kein Log vorhanden")
            return
        lines = self.procs.tail(proc.key, 30)
        if not lines:
            self.emit("warn", f"{proc.name}: Log ist leer ({proc.log_path})")
            return
        for line in lines:
            self.emit("error" if "error" in line.lower() else "info", f"{proc.name}| {line}")

    # -- Status / Doctor -------------------------------------------------

    def status_lines(self) -> list[str]:
        lines = list(self.config.summary())
        lines.append("Prozesse:")
        rows = self.procs.rows()
        if not rows:
            lines.append("  (keine)")
        for index, proc in enumerate(rows, start=1):
            origin = "extern" if proc.external else f"PID {proc.pid}"
            socket_info = f" socket={proc.socket}" if proc.socket else ""
            lines.append(
                f"  [{index:02d}] {proc.name:<16} {proc.status:<8} {origin}{socket_info}"
                + (f" - {proc.message}" if proc.message else "")
            )
        display = self.active_display()
        lines.append(f"Aktives Display: {display or '-'}")
        return lines

    def doctor_async(self) -> None:
        threading.Thread(target=self._doctor_worker, name="doctor", daemon=True).start()

    def _doctor_worker(self) -> None:
        try:
            results = run_doctor(self)
        except Exception as exc:  # noqa: BLE001
            self.emit("error", f"doctor fehlgeschlagen: {exc}")
            return
        self.last_doctor = results
        self.emit("info", "doctor:")
        for item in results:
            level = {"ok": "info", "warn": "warn", "fail": "error"}[item.level]
            self.emit(level, f"  {item.line()}")
        failures = sum(1 for item in results if item.level == "fail")
        self.emit(
            "ok" if failures == 0 else "error",
            f"doctor: {len(results) - failures}/{len(results)} Pruefungen in Ordnung",
        )

    def doctor(self) -> list[Diagnostic]:
        self.last_doctor = run_doctor(self)
        return self.last_doctor

    def reload_config(self) -> bool:
        try:
            new_config = load_config(self.paths.config, create=False)
        except ConfigError as exc:
            self.emit("error", "Konfiguration NICHT geladen - alte bleibt aktiv:")
            for error in exc.errors:
                self.emit("error", f"  {error}")
            return False
        self.config = new_config
        self.emit("ok", f"Konfiguration neu geladen: {new_config.path}")
        for warning in new_config.warnings:
            self.emit("warn", warning)
        return True

    # -- Persistenz ------------------------------------------------------

    def save_state(self) -> None:
        records = []
        for proc in self.procs.rows():
            if proc.external or not proc.alive():
                continue
            records.append(
                {
                    "name": proc.name,
                    "pid": proc.pid,
                    "start_ticks": proc.start_ticks,
                    "socket": proc.socket,
                }
            )
        payload = json.dumps({"processes": records}, indent=2)
        try:
            self.paths.state_dir.mkdir(parents=True, exist_ok=True)
            temporary = self._state_path.with_suffix(".tmp")
            temporary.write_text(payload, encoding="utf-8")
            temporary.replace(self._state_path)
        except OSError as exc:
            self.emit("warn", f"State konnte nicht gespeichert werden: {exc}")

    def _adopt_leftovers(self) -> None:
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for record in data.get("processes", []):
            name = str(record.get("name", ""))
            entry = self.config.entry(name)
            pid = record.get("pid")
            ticks = record.get("start_ticks")
            if entry is None or not isinstance(pid, int):
                continue
            if not pm.process_identity_matches(pid, ticks):
                self.emit(
                    "warn",
                    f"{name}: Prozess aus vorherigem Lauf (PID {pid}) ist nicht mehr aktiv",
                )
                continue
            self.procs.adopt(entry, pid, ticks, record.get("socket"))
            self.emit(
                "warn",
                f"{name}: PID {pid} aus vorherigem Lauf uebernommen (wird nicht beendet)",
            )

    # -- Herunterfahren --------------------------------------------------

    def shutdown(self) -> list[str]:
        self.emit("info", "Manager wird beendet")
        stop_names: list[str] = []
        for proc in self.procs.rows():
            if proc.external:
                continue
            entry = self.config.entry(proc.name)
            if entry is None:
                if self.settings.stop_on_exit:
                    stop_names.append(proc.name)
                continue
            if entry.should_stop_on_exit(self.settings):
                stop_names.append(proc.name)
        stopped = self.procs.stop_all(stop_names)
        self.save_state()
        with self._lock:
            threads = list(self._threads)
        for thread in threads:
            thread.join(timeout=2)
        kept = [
            proc.name
            for proc in self.procs.rows()
            if proc.alive() and (proc.external or proc.name not in stopped)
        ]
        if stopped:
            self.emit("ok", f"Beendet: {', '.join(stopped)}")
        if kept:
            self.emit("info", f"Laeuft weiter: {', '.join(kept)}")
        return stopped
