"""Ablageorte fuer Konfiguration, Logs und State."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _home() -> Path:
    return Path(os.environ.get("HOME", str(Path.home())))


def _xdg(name: str, default: str) -> Path:
    value = os.environ.get(name)
    if value:
        return Path(value)
    return _home() / default


@dataclass(frozen=True)
class Paths:
    config: Path
    data: Path
    state: Path
    logs: Path
    state_dir: Path
    runtime: Path

    @classmethod
    def from_env(
        cls,
        config: str | os.PathLike[str] | None = None,
        state: str | os.PathLike[str] | None = None,
        runtime: str | os.PathLike[str] | None = None,
    ) -> "Paths":
        config_path = Path(config).expanduser() if config else _xdg(
            "XDG_CONFIG_HOME", ".config"
        ) / "run_weston" / "config.toml"
        state_home = Path(state).expanduser() if state else _xdg(
            "XDG_STATE_HOME", ".local/state"
        ) / "run_weston"
        if runtime:
            runtime_dir = Path(runtime).expanduser()
        elif os.environ.get("XDG_RUNTIME_DIR"):
            runtime_dir = Path(os.environ["XDG_RUNTIME_DIR"])
        else:
            runtime_dir = Path(f"/run/user/{os.getuid()}")
        return cls(
            config=config_path,
            data=_xdg("XDG_DATA_HOME", ".local/share") / "run-weston",
            state=state_home,
            logs=state_home / "logs",
            state_dir=state_home / "state",
            runtime=runtime_dir,
        )

    def ensure(self) -> "Paths":
        for directory in (self.config.parent, self.data, self.logs, self.state_dir):
            directory.mkdir(parents=True, exist_ok=True)
        return self
