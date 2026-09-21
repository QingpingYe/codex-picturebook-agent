"""Strict, portable configuration loading for the Feishu knowledge store."""

import json
import os
import shutil
from pathlib import Path
from typing import Callable, Mapping

from models import KnowledgeConfig, SourceConfig, TargetConfig
from config_paths import resolve_config_path


class ConfigError(ValueError):
    """Raised when a knowledge-store configuration is unsafe or invalid."""

    status = "invalid_config"

    def to_dict(self) -> dict:
        return {"status": self.status, "error": str(self)}


class PlaceholderTargetTokenError(ConfigError):
    status = "placeholder_target_token"


_FIELDS = {
    "schema_version",
    "source",
    "target",
    "identity",
    "lock_ttl_minutes",
}
_SOURCE_FIELDS = {"space_id", "root_mode", "wiki_url"}
_TARGET_FIELDS = {"space_id", "root_token"}
_PLACEHOLDER_TARGET_TOKEN = "REPLACE_WITH_TARGET_ROOT_TOKEN"
_WINDOWS_CLI_FALLBACK = Path(r"D:\lark-cli\lark-cli.exe")


def load_config(
    config_path: str | os.PathLike[str] | None,
    workspace: str | os.PathLike[str] | None = None,
    environ: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> KnowledgeConfig:
    """Load exactly schema-v2 config, using explicit paths first.

    ``which`` is injectable solely to make portable candidate resolution testable.
    """
    environment = os.environ if environ is None else environ
    if config_path is None:
        resolved = resolve_config_path(None, workspace, environment)
        selected = resolved.path
    else:
        selected = Path(config_path)
    path = Path(selected)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ConfigError(f"configuration file not found: {path}") from error
    except (OSError, json.JSONDecodeError) as error:
        raise ConfigError(f"cannot read configuration: {error}") from error

    if not isinstance(raw, dict):
        raise ConfigError("configuration must be a JSON object")
    version = raw.get("schema_version")
    if isinstance(version, bool):
        raise ConfigError("schema_version must be 2")
    if version != 2:
        if version == 1:
            raise ConfigError("configuration schema v1 is deprecated")
        raise ConfigError("schema_version must be 2")
    unknown = set(raw) - _FIELDS
    missing = _FIELDS - set(raw)
    if unknown:
        raise ConfigError("unexpected configuration fields: " + ", ".join(sorted(unknown)))
    if missing:
        raise ConfigError("missing configuration fields: " + ", ".join(sorted(missing)))
    source = _nested_object(raw["source"], _SOURCE_FIELDS, "source")
    target = _nested_object(raw["target"], _TARGET_FIELDS, "target")

    source_space_id = _nonempty_string(source["space_id"], "source.space_id")
    source_wiki_url = _nonempty_string(source["wiki_url"], "source.wiki_url")
    if source["root_mode"] not in {"space", "node"}:
        raise ConfigError("source.root_mode must be 'space' or 'node'")
    target_space_id = _nonempty_string(target["space_id"], "target.space_id")
    target_root_token = _nonempty_string(target["root_token"], "target.root_token")
    if target_root_token == _PLACEHOLDER_TARGET_TOKEN:
        raise PlaceholderTargetTokenError(
            "target.root_token must not remain the placeholder value"
        )
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
    return KnowledgeConfig(
        SourceConfig(source_space_id, source["root_mode"], source_wiki_url),
        TargetConfig(target_space_id, target_root_token),
        "user", ttl, tuple(candidates),
    )


def _nonempty_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{field} must be a non-empty string")
    return value


def _nested_object(value: object, fields: set[str], name: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ConfigError(f"{name} must be a JSON object")
    unknown = set(value) - fields
    missing = fields - set(value)
    if unknown:
        raise ConfigError(f"unexpected {name} fields: " + ", ".join(sorted(unknown)))
    if missing:
        raise ConfigError(f"missing {name} fields: " + ", ".join(sorted(missing)))
    return value
