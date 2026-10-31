"""Kommandozeilen-Einstieg: TUI, doctor, status, logs, check."""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .config import ConfigError, load_config
from .logger import safe_name, tail_lines
from .manager import SessionManager
from .paths import Paths
from .tui import Tui


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_weston",
        description=(
            "Weston Session Manager: startet Weston verschachtelt in X11, "
            "verwaltet Niri/DMS/Wayland-Programme und wickelt Abhaengigkeiten ab."
        ),
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="tui",
        choices=("tui", "doctor", "status", "logs", "check"),
        help="Standard: tui (interaktive Oberflaeche)",
    )
    parser.add_argument(
        "target", nargs="?", help="Programmname fuer 'logs' (ohne Argument: Manager-Log)"
    )
    parser.add_argument(
        "-n", "--lines", type=int, default=40, help="Anzahl Logzeilen (Standard: 40)"
    )
    parser.add_argument("--config", help="Pfad zur config.toml")
    parser.add_argument("--state-dir", help="State-Verzeichnis fuer Logs/State")
    parser.add_argument("--runtime-dir", help="Abweichendes XDG_RUNTIME_DIR")
    parser.add_argument("--version", action="version", version=f"run_weston {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    paths = Paths.from_env(
        config=args.config, state=args.state_dir, runtime=args.runtime_dir
    )

    try:
        config = load_config(paths.config)
    except ConfigError as exc:
        print("Konfiguration fehlerhaft:", file=sys.stderr)
        for error in exc.errors:
            print(f"  - {error}", file=sys.stderr)
        print(f"\nDatei: {paths.config}", file=sys.stderr)
        return 2

    manager = SessionManager(paths=paths, config=config)

    if args.command == "check":
        print(f"Konfiguration OK: {config.path}")
        for warning in config.warnings:
            print(f"  Hinweis: {warning}")
        return 0

    if args.command == "status":
        print("\n".join(manager.status_lines()))
        return 0

    if args.command == "doctor":
        results = manager.doctor()
        for item in results:
            print(item.line())
        failed = any(item.level == "fail" for item in results)
        return 1 if failed else 0

    if args.command == "logs":
        if not args.target:
            lines = tail_lines(paths.logs / "manager.log", args.lines)
        else:
            proc = manager.procs.get(args.target)
            path = (
                proc.log_path
                if proc is not None
                else paths.logs / f"{safe_name(args.target)}.log"
            )
            lines = tail_lines(path, args.lines)
        print("\n".join(lines))
        return 0

    return Tui(manager).run()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
