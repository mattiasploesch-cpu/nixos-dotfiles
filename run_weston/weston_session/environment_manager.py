"""Umgebungen fuer Programme, die in Weston bzw. einem anderen Compositor laufen."""

from __future__ import annotations

import os
from typing import Mapping

# Variablen, die einen verschachtelten X11-Start nicht stoeren.
BASE_KEYS = (
    "HOME",
    "USER",
    "LOGNAME",
    "SHELL",
    "PATH",
    "LANG",
    "LC_ALL",
    "TERM",
    "TZ",
    "DISPLAY",
    "XAUTHORITY",
    "DBUS_SESSION_BUS_ADDRESS",
    "SSH_AUTH_SOCK",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_CACHE_HOME",
    "XDG_STATE_HOME",
    "XDG_CURRENT_DESKTOP",
    "XDG_SESSION_DESKTOP",
    "XDG_SESSION_TYPE",
    "XDG_SESSION_ID",
    "XDG_SEAT",
    "XDG_VTNR",
)


class EnvironmentManager:
    """Baut Prozessumgebungen nach der Regel: Abhaengigkeit -> Kind.

    XDG_RUNTIME_DIR kommt immer aus der Laufzeitumgebung des Benutzers,
    WAYLAND_DISPLAY aus dem Socket des Compositors, den ein Eintrag benoetigt.
    """

    def __init__(
        self,
        runtime_dir: str | os.PathLike[str] | None = None,
        base_env: Mapping[str, str] | None = None,
    ) -> None:
        if runtime_dir is not None:
            self.runtime_dir = str(runtime_dir)
        else:
            self.runtime_dir = os.environ.get(
                "XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"
            )
        source = base_env if base_env is not None else os.environ
        self._base = {key: source[key] for key in BASE_KEYS if key in source}

    def base(self) -> dict[str, str]:
        env = dict(self._base)
        env["XDG_RUNTIME_DIR"] = self.runtime_dir
        return env

    def for_entry(
        self,
        command: list[str] | tuple[str, ...],
        *,
        backend: str | None = None,
        wayland_display: str | None = None,
        extra_env: Mapping[str, str] | None = None,
    ) -> dict[str, str]:
        env = self.base()
        # Ein x11-Backend darf keinen geerbten Wayland-Socket sehen.
        if backend == "x11":
            env.pop("WAYLAND_DISPLAY", None)
        elif wayland_display:
            env["WAYLAND_DISPLAY"] = wayland_display
        else:
            env.pop("WAYLAND_DISPLAY", None)
        if extra_env:
            env.update({str(key): str(value) for key, value in extra_env.items()})
        env["XDG_RUNTIME_DIR"] = self.runtime_dir
        return env
