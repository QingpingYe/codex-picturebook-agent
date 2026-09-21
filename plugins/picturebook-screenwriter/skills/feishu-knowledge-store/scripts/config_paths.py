"""Portable resolution paths for Feishu knowledge-base configuration."""

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class ResolvedConfigPath:
    path: Path
    origin: str
    searched: tuple[Path, ...]


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

    base = Path(workspace) if workspace is not None else Path.cwd()
    home_path = Path(home) if home is not None else Path.home()
    candidates = (
        (base / "feishu-knowledge-base.json", "workspace"),
        (home_path / ".picturebook-screenwriter" / "feishu-knowledge-base.json", "user"),
    )
    searched = tuple(path for path, _ in candidates)
    for path, origin in candidates:
        if path.is_file():
            return ResolvedConfigPath(path, origin, searched)
    raise ConfigResolutionError(
        "configuration file not found; create <workspace>/feishu-knowledge-base.json "
        "or set PICTUREBOOK_KB_CONFIG",
        searched=searched,
    )
