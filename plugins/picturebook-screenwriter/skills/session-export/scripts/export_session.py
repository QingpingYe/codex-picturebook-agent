"""Codex-local session audit bundle builder."""

import json
import re
from datetime import datetime, timezone
from pathlib import Path


_SECRET_KEYS = {"api_key", "apikey", "authorization", "password", "secret", "token"}


def build_manifest(session_id: str, messages: list, files: list, images: list) -> dict:
    return {
        "source": {"session_id": session_id},
        "summary": {
            "messages": len(messages),
            "files": len(files),
            "images": len(images),
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _assert_no_secret_keys(value, path: str = "input") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if str(key).lower() in _SECRET_KEYS:
                raise ValueError(f"secret field not allowed: {child_path}")
            _assert_no_secret_keys(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_no_secret_keys(child, f"{path}[{index}]")


def export_session(
    session_id: str,
    messages: list,
    files: list,
    images: list,
    output_dir: Path,
    plugin_root: Path,
) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", session_id):
        raise ValueError("session id must contain only ASCII letters, digits, _ or -")

    output_dir = Path(output_dir)
    if not output_dir.is_absolute():
        raise ValueError("output directory must be an absolute path")

    _assert_no_secret_keys(messages, "messages")
    _assert_no_secret_keys(files, "files")
    _assert_no_secret_keys(images, "images")

    plugin_root = Path(plugin_root).resolve()
    bundle = output_dir / f"{session_id}-audit"
    resolved_bundle = bundle.resolve()
    if resolved_bundle == plugin_root or plugin_root in resolved_bundle.parents:
        raise ValueError("output directory must be outside the plugin")

    bundle.mkdir(parents=True, exist_ok=True)
    payloads = {
        "manifest.json": build_manifest(session_id, messages, files, images),
        "messages.json": messages,
        "files.json": files,
        "images.json": images,
    }
    for name, payload in payloads.items():
        (bundle / name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return bundle
