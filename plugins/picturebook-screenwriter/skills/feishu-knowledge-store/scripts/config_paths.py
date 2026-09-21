"""Portable resolution paths for Feishu knowledge-base configuration."""

from dataclasses import dataclass
import os
from pathlib import Path
import sys
from typing import Mapping


@dataclass(frozen=True)
class ResolvedConfigPath:
    path: Path
    origin: str
    searched: tuple[Path, ...]


_CONFIG_NAME = "feishu-knowledge-base.json"


class ConfigResolutionError(ValueError):
    def __init__(self, message: str, *, status: str = "missing_config",
                 searched: tuple[Path, ...] = (), origin: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.searched = searched
        self.origin = origin

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "error": str(self),
            "origin": self.origin,
            "searched": [str(path) for path in self.searched],
        }


def resolve_config_path(
    explicit_path: str | Path | None,
    workspace: str | Path | None,
    environ: Mapping[str, str] | None = None,
    home: str | Path | None = None,
    system: str | None = None,
) -> ResolvedConfigPath:
    environment = os.environ if environ is None else environ
    direct = explicit_path
    if direct is None:
        direct = environment.get("PICTUREBOOK_KB_CONFIG") or None
    if direct is not None:
        path = Path(direct)
        origin = "explicit" if explicit_path is not None else "environment"
        if path.is_file():
            return ResolvedConfigPath(path, origin, (path,))
        raise ConfigResolutionError(f"configuration file not found: {path}", searched=(path,))

    workspace_path = Path(workspace) if workspace is not None else Path.cwd()
    home_path = Path(home) if home is not None else Path.home()

    chain = _workspace_chain(workspace_path)
    searched = tuple(base / _CONFIG_NAME for base in chain)
    for index, base in enumerate(chain):
        path = base / _CONFIG_NAME
        if path.is_file():
            origin = "workspace" if index == 0 else "ancestor"
            return ResolvedConfigPath(path, origin, searched)

    for index, path in enumerate(user_config_candidates(environment, home_path, system)):
        if path.is_file():
            origin = "user" if index == 0 else "user-legacy"
            return ResolvedConfigPath(path, origin, (*searched, *user_config_candidates(environment, home_path, system)))
    raise ConfigResolutionError(
        "configuration file not found; create <workspace>/feishu-knowledge-base.json "
        "or set PICTUREBOOK_KB_CONFIG",
        searched=(*searched, *user_config_candidates(environment, home_path, system)),
    )


def _workspace_chain(workspace: str | Path) -> tuple[Path, ...]:
    current = Path(workspace).expanduser().resolve()
    chain = []
    while True:
        chain.append(current)
        if current.parent == current:
            break
        current = current.parent
    return tuple(chain)


def user_config_candidates(
    environ: Mapping[str, str] | None = None,
    home: str | Path | None = None,
    system: str | None = None,
) -> tuple[Path, ...]:
    environment = os.environ if environ is None else environ
    home_path = Path(home) if home is not None else Path.home()
    system_name = (
        system
        or ("windows" if os.name == "nt" else "macos" if sys.platform == "darwin" else "linux")
    ).lower()
    if system_name == "windows":
        appdata = environment.get("APPDATA") or home_path / "AppData" / "Roaming"
        standard = Path(appdata) / "picturebook-screenwriter"
    elif system_name == "macos":
        standard = home_path / "Library" / "Application Support" / "picturebook-screenwriter"
    else:
        config_home = environment.get("XDG_CONFIG_HOME") or home_path / ".config"
        standard = Path(config_home) / "picturebook-screenwriter"
    legacy = home_path / ".picturebook-screenwriter"
    return (
        standard / _CONFIG_NAME,
        legacy / _CONFIG_NAME,
    )
