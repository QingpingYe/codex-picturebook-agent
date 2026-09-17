"""Shared immutable models for the Feishu authoritative knowledge store."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class KnowledgeConfig:
    source_wiki_url: str
    target_root_token: str
    identity: Literal["user"]
    lock_ttl_minutes: int
    cli_candidates: tuple[Path, ...]


@dataclass(frozen=True)
class IndexEntry:
    key: str
    doc_token: str
    wiki_node_token: str
    source_revisions: dict[str, str]
    last_ai_revision_id: int
    last_seen_revision_id: int
    status: Literal["published", "needs_review", "archived"]
