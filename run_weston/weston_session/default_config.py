"""Kanonische Standardkonfiguration fuer run_weston.

Die grosse Programmliste steht in `default-config.toml` neben diesem Paket.
Dieselbe Datei lesen die Nix-Module per `lib.importTOML`, damit es nur eine
Quelle fuer die Standardbelegung gibt.

Reihenfolge beim Laden:
  1. `$RUN_WESTON_DEFAULT_CONFIG` (setzt das Nix-Paket)
  2. `default-config.toml` neben diesem Modul (Repo-Betrieb)
  3. eingebauter Minimalbestand, damit der Manager immer startfaehig ist
"""

from __future__ import annotations

import os
from pathlib import Path

FALLBACK_CONFIG = '''# run_weston - Minimalbestand (default-config.toml nicht gefunden)

[settings]
default_compositor = "weston"
startup_timeout = 15

[compositors.weston]
command = ["weston", "--backend=x11", "--socket=wayland-weston"]
socket = "wayland-weston"
backend = "x11"
ready_check = "socket"
stop_on_exit = false

[programs.niri]
command = ["niri"]
requires = ["weston"]
socket_detection = "log"
ready_pattern = 'listening on Wayland socket: (?P<socket>\\S+)'
ready_check = "log"
startup_timeout = 20

[programs.dms]
command = ["dms", "run"]
requires = ["niri"]

[unknown]
allow = false
env = "niri"
ready_check = "process"
'''


def _candidates() -> list[Path]:
    paths: list[Path] = []
    from_env = os.environ.get("RUN_WESTON_DEFAULT_CONFIG")
    if from_env:
        paths.append(Path(from_env))
    here = Path(__file__)
    # Repo-Layout: run_weston/default-config.toml (ein Verzeichnis hoeher).
    paths.append(here.parent / "default-config.toml")
    paths.append(here.parent.parent / "default-config.toml")
    return paths


def load_default_config() -> str:
    """Liest die Standardkonfiguration; faellt auf den Minimalbestand zurueck."""
    for path in _candidates():
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if text.strip():
            return text
    return FALLBACK_CONFIG


DEFAULT_CONFIG = load_default_config()
