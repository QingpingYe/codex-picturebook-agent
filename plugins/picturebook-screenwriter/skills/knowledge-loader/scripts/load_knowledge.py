"""Read-only retrieval from the authoritative Feishu Wiki knowledge store."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from typing import Any, Mapping

SCRIPT_DIR = Path(__file__).resolve().parent
STORE_SCRIPTS = SCRIPT_DIR.parent.parent / "feishu-knowledge-store" / "scripts"
if str(STORE_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(STORE_SCRIPTS))

from page_codec import parse_remote_page, PageCodecError


@dataclass(frozen=True)
class KnowledgeQuery:
    project_id: str
    terms: tuple[str, ...] = ()
    page_types: tuple[str, ...] = ()
    limit: int = 8


@dataclass(frozen=True)
class KnowledgeEvidence:
    key: str
    doc_token: str
    revision_id: int
    title: str
    content: str
    source_revisions: dict[str, str]


@dataclass(frozen=True)
class KnowledgeEvidenceBundle:
    items: tuple[KnowledgeEvidence, ...]
    warnings: tuple[str, ...]
    offline: bool
    fetched_at: str


class KnowledgeLoader:
    def __init__(self, control_plane: Any, cli: Any, cache_store: Any = None,
                 cached_bundle: KnowledgeEvidenceBundle | None = None) -> None:
        self.control_plane = control_plane
        self.cli = cli
        self.cache_store = cache_store
        self.cached_bundle = cached_bundle

    def load(self, query: KnowledgeQuery,
             allow_offline_cache: bool = False) -> KnowledgeEvidenceBundle:
        try:
            index = self.control_plane.read_index()
        except Exception as error:
            if allow_offline_cache and self.cached_bundle is not None:
                warnings = list(self.cached_bundle.warnings)
                warnings.insert(0, "目标飞书知识库不可用，正在使用最后确认的本地缓存；它不是权威版本。")
                return KnowledgeEvidenceBundle(
                    items=self.cached_bundle.items,
                    warnings=tuple(warnings),
                    offline=True,
                    fetched_at=self.cached_bundle.fetched_at,
                )
            raise error

        candidates = [
            entry for entry in index.values()
            if query.project_id in entry.key
            and (not query.page_types or entry.key.split("/")[-1] in query.page_types)
        ]

        items = []
        warnings = []
        for entry in candidates:
            try:
                raw = self.cli.fetch_doc(entry.doc_token)
                document = raw.get("data", {}).get("document", {})
                page = parse_remote_page(document.get("content", ""))
                score = sum(1 for term in query.terms if term in page.body)
                if query.terms and score == 0:
                    continue
                items.append((score, entry, page, document))
                if entry.status == "needs_review":
                    warnings.append(f"{entry.key} 存在待处理冲突")
            except PageCodecError:
                warnings.append(f"{entry.key} 的系统元数据无效")

        items.sort(key=lambda value: (-value[0], value[1].key))
        evidence = tuple(
            KnowledgeEvidence(
                key=entry.key,
                doc_token=entry.doc_token,
                revision_id=int(document["revision_id"]),
                title=entry.key.split("/")[-1],
                content=page.body,
                source_revisions=dict(entry.source_revisions),
            )
            for score, entry, page, document in items[:query.limit]
        )

        bundle = KnowledgeEvidenceBundle(
            items=evidence,
            warnings=tuple(warnings),
            offline=False,
            fetched_at=_now(),
        )
        if self.cache_store is not None:
            self.cache_store.save(_bundle_to_dict(bundle))
        return bundle


def _bundle_to_dict(bundle: KnowledgeEvidenceBundle) -> dict[str, Any]:
    return {
        "items": [
            {
                "key": item.key,
                "doc_token": item.doc_token,
                "revision_id": item.revision_id,
                "title": item.title,
                "content": item.content,
                "source_revisions": item.source_revisions,
            }
            for item in bundle.items
        ],
        "warnings": list(bundle.warnings),
        "offline": bundle.offline,
        "fetched_at": bundle.fetched_at,
    }


def _now() -> str:
    return datetime.now(timezone(timedelta(hours=8))).isoformat()


if __name__ == "__main__":
    raise SystemExit("use skills/knowledge-loader/SKILL.md, not this module directly")
