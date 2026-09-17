"""Per-user cache for the last successful authoritative knowledge read."""

import json
from pathlib import Path
from typing import Any


class KnowledgeCache:
    def __init__(self, workspace: Path, user: str) -> None:
        self.path = workspace / ".picturebook-screenwriter" / "cache" / user / "knowledge-bundle.json"

    def save(self, bundle: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(bundle, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )

    def load(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
