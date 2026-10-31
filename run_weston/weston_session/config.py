"""Konfiguration laden, validieren und die Standarddatei anlegen."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .default_config import DEFAULT_CONFIG
from .resolver import find_cycles

READY_CHECKS = frozenset({"process", "socket", "log", "command", "exit"})
SOCKET_DETECTIONS = frozenset({"auto", "cmdline", "log", "scan", "none"})
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]*$")

SETTINGS_KEYS = (
    "default_compositor",
    "startup_timeout",
    "health_check_interval",
    "kill_timeout",
    "reuse_existing",
    "stop_on_exit",
    "allow_unknown_programs",
)


class ConfigError(Exception):
    """Sammlung aller Konfigurationsfehler mit Position."""

    def __init__(self, errors: list[str], warnings: list[str] | None = None) -> None:
        self.errors = list(errors)
        self.warnings = list(warnings or [])
        super().__init__("\n".join(self.errors))


def _line_hint(text: str, section: str, key: str | None = None) -> str:
    """Menschenlesbarer Hinweis auf eine Zeile, z. B. " (Zeile 12)"."""
    lines = text.splitlines()
    header = f"[{section}]"
    start = None
    for index, line in enumerate(lines):
        if line.strip().startswith(header):
            start = index
            break
    if start is None:
        return ""
    if key is None:
        return f" (Zeile {start + 1})"
    for index in range(start + 1, len(lines)):
        stripped = lines[index].strip()
        if stripped.startswith("["):
            break
        if re.match(rf"^{re.escape(key)}\s*=", stripped):
            return f" (Zeile {index + 1})"
    return f" (Zeile {start + 1})"


@dataclass(frozen=True)
class Settings:
    default_compositor: str = "weston"
    startup_timeout: float = 15.0
    health_check_interval: float = 1.0
    kill_timeout: float = 5.0
    reuse_existing: bool = True
    stop_on_exit: bool = True
    allow_unknown_programs: bool = False


@dataclass(frozen=True)
class UnknownRules:
    allow: bool = False
    env: str | None = None
    ready_check: str = "process"


@dataclass(frozen=True)
class Entry:
    """Ein Compositor oder Programm aus der Konfiguration."""

    name: str
    kind: str  # "compositors" oder "programs"
    command: tuple[str, ...]
    requires: tuple[str, ...] = ()
    socket: str | None = None
    socket_detection: str = "auto"
    ready_pattern: str | None = None
    backend: str | None = None
    ready_check: str = "process"
    ready_command: tuple[str, ...] | None = None
    startup_timeout: float | None = None
    env: Mapping[str, str] = field(default_factory=dict)
    stop_on_exit: bool | None = None

    @property
    def is_compositor(self) -> bool:
        return self.kind == "compositors"

    def timeout(self, settings: Settings) -> float:
        if self.startup_timeout is not None:
            return self.startup_timeout
        return settings.startup_timeout

    def should_stop_on_exit(self, settings: Settings) -> bool:
        if self.stop_on_exit is not None:
            return self.stop_on_exit
        return settings.stop_on_exit


@dataclass
class Config:
    path: str
    settings: Settings = field(default_factory=Settings)
    compositors: dict[str, Entry] = field(default_factory=dict)
    programs: dict[str, Entry] = field(default_factory=dict)
    unknown: UnknownRules = field(default_factory=UnknownRules)
    warnings: list[str] = field(default_factory=list)
    raw: str = ""

    def entries(self) -> dict[str, Entry]:
        entries = dict(self.compositors)
        entries.update(self.programs)
        return entries

    def entry(self, name: str) -> Entry | None:
        return self.entries().get(name)

    def require(self, name: str) -> Entry:
        entry = self.entry(name)
        if entry is None:
            raise KeyError(name)
        return entry

    def names(self) -> list[str]:
        return sorted(self.entries())

    def summary(self) -> list[str]:
        lines = [
            f"Konfiguration: {self.path}",
            f"Compositoren: {', '.join(sorted(self.compositors)) or '-'}",
            f"Programme:    {', '.join(sorted(self.programs)) or '-'}",
            f"Default:      {self.settings.default_compositor}",
            f"Timeouts:     startup {self.settings.startup_timeout:g}s, "
            f"kill {self.settings.kill_timeout:g}s",
            f"Reuse:        {'ja' if self.settings.reuse_existing else 'nein'}",
        ]
        if self.warnings:
            lines.append("Hinweise:")
            lines.extend(f"  - {warning}" for warning in self.warnings)
        return lines


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def _as_str(raw: Any, label: str, hint: str, errors: list[str]) -> str | None:
    if not isinstance(raw, str) or not raw.strip():
        errors.append(f"{label}: erwartet wird ein nicht-leerer String{hint}")
        return None
    return raw.strip()


def _as_number(raw: Any, label: str, hint: str, errors: list[str]) -> float | None:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        errors.append(f"{label}: erwartet wird eine Zahl{hint}")
        return None
    if raw <= 0:
        errors.append(f"{label}: muss groesser als 0 sein{hint}")
        return None
    return float(raw)


def _as_bool(raw: Any, label: str, hint: str, errors: list[str]) -> bool | None:
    if not isinstance(raw, bool):
        errors.append(f"{label}: erwartet wird true oder false{hint}")
        return None
    return raw


def _as_argv(raw: Any, label: str, hint: str, errors: list[str]) -> tuple[str, ...] | None:
    if isinstance(raw, str):
        errors.append(
            f"{label}: muss eine Liste sein, z. B. [\"programm\", \"--flag\"]{hint}"
        )
        return None
    if not isinstance(raw, list) or not raw:
        errors.append(f"{label}: nicht-leere Liste von Argumenten erwartet{hint}")
        return None
    items: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item:
            errors.append(f"{label}: alle Argumente muessen nicht-leere Strings sein{hint}")
            return None
        items.append(item)
    return tuple(items)


def _parse_settings(
    raw: Any, text: str, errors: list[str], warnings: list[str]
) -> Settings:
    settings = Settings()
    if raw is None:
        return settings
    if not isinstance(raw, dict):
        errors.append(f"[settings]: erwartet wird eine Tabelle{_line_hint(text, 'settings')}")
        return settings

    values: dict[str, Any] = {}
    for key, value in raw.items():
        label = f"[settings] {key}"
        hint = _line_hint(text, "settings", key)
        if key not in SETTINGS_KEYS:
            warnings.append(f"Unbekannte Option [settings] {key} wird ignoriert{hint}")
            continue
        if key == "default_compositor":
            parsed = _as_str(value, label, hint, errors)
        elif key in ("startup_timeout", "kill_timeout", "health_check_interval"):
            parsed = _as_number(value, label, hint, errors)
        else:
            parsed = _as_bool(value, label, hint, errors)
        if parsed is not None:
            values[key] = parsed

    return Settings(
        default_compositor=values.get("default_compositor", settings.default_compositor),
        startup_timeout=values.get("startup_timeout", settings.startup_timeout),
        health_check_interval=values.get(
            "health_check_interval", settings.health_check_interval
        ),
        kill_timeout=values.get("kill_timeout", settings.kill_timeout),
        reuse_existing=values.get("reuse_existing", settings.reuse_existing),
        stop_on_exit=values.get("stop_on_exit", settings.stop_on_exit),
        allow_unknown_programs=values.get(
            "allow_unknown_programs", settings.allow_unknown_programs
        ),
    )


def _parse_unknown(raw: Any, text: str, errors: list[str]) -> UnknownRules:
    rules = UnknownRules()
    if raw is None:
        return rules
    if not isinstance(raw, dict):
        errors.append(f"[unknown]: erwartet wird eine Tabelle{_line_hint(text, 'unknown')}")
        return rules

    allow = rules.allow
    env = rules.env
    ready_check = rules.ready_check
    for key, value in raw.items():
        label = f"[unknown] {key}"
        hint = _line_hint(text, "unknown", key)
        if key == "allow":
            parsed = _as_bool(value, label, hint, errors)
            if parsed is not None:
                allow = parsed
        elif key == "env":
            parsed = _as_str(value, label, hint, errors)
            if parsed is not None:
                env = parsed
        elif key == "ready_check":
            parsed = _as_str(value, label, hint, errors)
            if parsed is not None:
                if parsed not in READY_CHECKS:
                    errors.append(
                        f"{label}: unbekannter ready_check {parsed!r} "
                        f"(erlaubt: {', '.join(sorted(READY_CHECKS))}){hint}"
                    )
                else:
                    ready_check = parsed
        else:
            errors.append(f"{label}: unbekannte Option{hint}")
    return UnknownRules(allow=allow, env=env, ready_check=ready_check)


def _parse_entry(
    kind: str, name: str, raw: Any, text: str, errors: list[str]
) -> Entry | None:
    section = f"{kind}.{name}"
    hint = _line_hint(text, section)
    if not NAME_RE.match(name):
        errors.append(f"[{section}]: Name enthaelt unerlaubte Zeichen{hint}")
        return None
    if not isinstance(raw, dict):
        errors.append(f"[{section}]: erwartet wird eine Tabelle{hint}")
        return None

    def field_hint(key: str) -> str:
        return _line_hint(text, section, key)

    command = _as_argv(raw.get("command"), f"[{section}] command", field_hint("command"), errors)
    if command is None:
        errors.append(f"[{section}]: ohne command ist der Eintrag nicht startbar{hint}")
        return None

    requires: tuple[str, ...] = ()
    if "requires" in raw:
        requires_raw = raw["requires"]
        req_hint = field_hint("requires")
        if isinstance(requires_raw, str):
            errors.append(
                f"[{section}] requires: muss eine Liste sein, z. B. [\"weston\"]{req_hint}"
            )
        elif not isinstance(requires_raw, list):
            errors.append(f"[{section}] requires: Liste von Namen erwartet{req_hint}")
        else:
            items: list[str] = []
            for item in requires_raw:
                if not isinstance(item, str) or not item:
                    errors.append(
                        f"[{section}] requires: alle Eintraege muessen Namen sein{req_hint}"
                    )
                    items = []
                    break
                items.append(item)
            requires = tuple(items)

    ready_check = "socket" if kind == "compositors" else "process"
    if "ready_check" in raw:
        parsed = _as_str(raw["ready_check"], f"[{section}] ready_check", field_hint("ready_check"), errors)
        if parsed is not None:
            if parsed not in READY_CHECKS:
                errors.append(
                    f"[{section}] ready_check: unbekannter Wert {parsed!r} "
                    f"(erlaubt: {', '.join(sorted(READY_CHECKS))})"
                    f"{field_hint('ready_check')}"
                )
            else:
                ready_check = parsed

    ready_command = None
    if "ready_command" in raw:
        ready_command = _as_argv(
            raw["ready_command"],
            f"[{section}] ready_command",
            field_hint("ready_command"),
            errors,
        )
    if ready_check == "command" and ready_command is None:
        errors.append(
            f"[{section}]: ready_check = \"command\" braucht ein ready_command{hint}"
        )

    ready_pattern = None
    if "ready_pattern" in raw:
        ready_pattern = _as_str(
            raw["ready_pattern"], f"[{section}] ready_pattern", field_hint("ready_pattern"), errors
        )
        if ready_pattern is not None:
            try:
                re.compile(ready_pattern)
            except re.error as exc:
                errors.append(
                    f"[{section}] ready_pattern: ungueltiger regulaerer Ausdruck "
                    f"({exc}){field_hint('ready_pattern')}"
                )
                ready_pattern = None

    socket_detection = "auto"
    if "socket_detection" in raw:
        parsed = _as_str(
            raw["socket_detection"],
            f"[{section}] socket_detection",
            field_hint("socket_detection"),
            errors,
        )
        if parsed is not None:
            if parsed not in SOCKET_DETECTIONS:
                errors.append(
                    f"[{section}] socket_detection: unbekannter Wert {parsed!r} "
                    f"(erlaubt: {', '.join(sorted(SOCKET_DETECTIONS))})"
                    f"{field_hint('socket_detection')}"
                )
            else:
                socket_detection = parsed

    socket_name = None
    if "socket" in raw:
        socket_name = _as_str(
            raw["socket"], f"[{section}] socket", field_hint("socket"), errors
        )

    startup_timeout = None
    if "startup_timeout" in raw:
        startup_timeout = _as_number(
            raw["startup_timeout"],
            f"[{section}] startup_timeout",
            field_hint("startup_timeout"),
            errors,
        )

    backend = None
    if "backend" in raw:
        backend = _as_str(raw["backend"], f"[{section}] backend", field_hint("backend"), errors)

    env: dict[str, str] = {}
    if "env" in raw:
        env_raw = raw["env"]
        env_hint = field_hint("env")
        if not isinstance(env_raw, dict):
            errors.append(f"[{section}] env: erwartet wird eine Tabelle{env_hint}")
        else:
            for key, value in env_raw.items():
                if not isinstance(value, str):
                    errors.append(
                        f"[{section}] env.{key}: erwartet wird ein String{env_hint}"
                    )
                    continue
                env[str(key)] = value

    stop_on_exit = None
    if "stop_on_exit" in raw:
        stop_on_exit = _as_bool(
            raw["stop_on_exit"], f"[{section}] stop_on_exit", field_hint("stop_on_exit"), errors
        )

    return Entry(
        name=name,
        kind=kind,
        command=command,
        requires=requires,
        socket=socket_name,
        socket_detection=socket_detection,
        ready_pattern=ready_pattern,
        backend=backend,
        ready_check=ready_check,
        ready_command=ready_command,
        startup_timeout=startup_timeout,
        env=env,
        stop_on_exit=stop_on_exit,
    )


def parse_config(text: str, path: str | Path = "config.toml") -> Config:
    """Parst und validiert den TOML-Text; wirft ConfigError mit allen Fehlern."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError([f"{path}: ungueltige TOML-Syntax: {exc}"]) from exc
    if not isinstance(data, dict):
        raise ConfigError([f"{path}: TOML-Dokument muss eine Tabelle sein"])

    errors: list[str] = []
    warnings: list[str] = []

    settings = _parse_settings(data.get("settings"), text, errors, warnings)
    unknown = _parse_unknown(data.get("unknown"), text, errors)

    entries: dict[str, Entry] = {}
    compositors: dict[str, Entry] = {}
    programs: dict[str, Entry] = {}

    for kind in ("compositors", "programs"):
        table = data.get(kind, {})
        if table is None:
            continue
        if not isinstance(table, dict):
            errors.append(f"[{kind}]: erwartet wird eine Tabelle{_line_hint(text, kind)}")
            continue
        for name, raw in table.items():
            if name in entries:
                errors.append(
                    f"[{kind}.{name}]: Name ist bereits als "
                    f"{entries[name].kind[:-1]} definiert"
                )
                continue
            entry = _parse_entry(kind, name, raw, text, errors)
            if entry is not None:
                entries[name] = entry
                (compositors if kind == "compositors" else programs)[name] = entry

    for entry in entries.values():
        for dependency in entry.requires:
            if dependency not in entries:
                errors.append(
                    f"[{entry.kind}.{entry.name}] requires: "
                    f"{dependency!r} ist nicht definiert"
                    f"{_line_hint(text, f'{entry.kind}.{entry.name}', 'requires')}"
                )

    if settings.default_compositor not in compositors:
        if settings.default_compositor in entries:
            errors.append(
                f"[settings] default_compositor: {settings.default_compositor!r} "
                "ist kein Compositor"
            )
        elif compositors:
            errors.append(
                f"[settings] default_compositor: {settings.default_compositor!r} "
                "ist nicht definiert"
            )

    for cycle in find_cycles(entries):
        errors.append(f"Zyklische Abhaengigkeit: {' -> '.join(cycle)}")

    if errors:
        raise ConfigError(errors, warnings)

    return Config(
        path=str(path),
        settings=settings,
        compositors=compositors,
        programs=programs,
        unknown=unknown,
        warnings=warnings,
        raw=text,
    )


def write_default_config(path: str | Path) -> Path:
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(DEFAULT_CONFIG, encoding="utf-8")
    return target


def load_config(path: str | Path = "config.toml", create: bool = True) -> Config:
    """Laedt die Konfiguration; legt die Standarddatei beim ersten Start an."""
    target = Path(path).expanduser()
    if not target.exists():
        if not create:
            raise ConfigError([f"Konfigurationsdatei fehlt: {target}"])
        existing = target.parent.glob("*")
        try:
            write_default_config(target)
        except OSError as exc:
            raise ConfigError([f"Konfiguration konnte nicht angelegt werden: {exc}"])
        del existing
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError([f"Konfiguration nicht lesbar ({target}): {exc}"]) from exc
    return parse_config(text, target)
