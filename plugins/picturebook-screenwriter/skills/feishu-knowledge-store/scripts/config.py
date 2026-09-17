"""Strict, portable configuration loading for the Feishu knowledge store."""

import json
import os
import shutil
from pathlib import Path
from typing import Callable, Mapping

from models import KnowledgeConfig


class ConfigError(ValueError):
    """Raised when a knowledge-store configuration is unsafe or invalid."""


_FIELDS = {
    "schema_version",
    "source_wiki_url",
    "target_root_token",
    "identity",
    "lock_ttl_minutes",
}
_DEFAULT_CONFIG_NAME = "feishu-knowledge-base.json"
_WINDOWS_CLI_FALLBACK = Path(r"D:\lark-cli\lark-cli.exe")


def load_config(
    config_path: str | os.PathLike[str] | None,
    workspace: str | os.PathLike[str],
    environ: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> KnowledgeConfig:
    """Load exactly version-one config, preferring the environment path.

    ``which`` is injectable solely to make portable candidate resolution testable.
    """
    environment = os.environ if environ is None else environ
    selected = environment.get("PICTUREBOOK_KB_CONFIG") or config_path
    if selected is None:
        selected = Path(workspace) / _DEFAULT_CONFIG_NAME
    path = Path(selected)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ConfigError(f"configuration file not found: {path}") from error
    except (OSError, json.JSONDecodeError) as error:
        raise ConfigError(f"cannot read configuration: {error}") from error

    if not isinstance(raw, dict):
        raise ConfigError("configuration must be a JSON object")
    unknown = set(raw) - _FIELDS
    missing = _FIELDS - set(raw)
    if unknown:
        raise ConfigError("unexpected configuration fields: " + ", ".join(sorted(unknown)))
    if missing:
        raise ConfigError("missing configuration fields: " + ", ".join(sorted(missing)))
    if raw["schema_version"] != 1:
        raise ConfigError("schema_version must be 1")

    source_wiki_url = _nonempty_string(raw["source_wiki_url"], "source_wiki_url")
    target_root_token = _nonempty_string(raw["target_root_token"], "target_root_token")
    if raw["identity"] != "user":
        raise ConfigError("identity must be 'user'")
    ttl = raw["lock_ttl_minutes"]
    if isinstance(ttl, bool) or not isinstance(ttl, int) or not 15 <= ttl <= 120:
        raise ConfigError("lock_ttl_minutes must be an integer from 15 to 120")

    candidates: list[Path] = []
    for candidate in (environment.get("LARK_CLI_PATH"), which("lark-cli"), str(_WINDOWS_CLI_FALLBACK)):
        if candidate:
            candidate_path = Path(candidate)
            if candidate_path not in candidates:
                candidates.append(candidate_path)
    return KnowledgeConfig(source_wiki_url, target_root_token, "user", ttl, tuple(candidates))


def _nonempty_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{field} must be a non-empty string")
    return value
