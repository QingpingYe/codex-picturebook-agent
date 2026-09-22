"""Read-only retrieval from the authoritative Feishu Wiki knowledge store."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from typing import Any, Literal

SCRIPT_DIR = Path(__file__).resolve().parent
STORE_SCRIPTS = SCRIPT_DIR.parent.parent / "feishu-knowledge-store" / "scripts"
if str(STORE_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(STORE_SCRIPTS))

from page_codec import parse_remote_page, PageCodecError


@dataclass(frozen=True)
class KnowledgeQuery:
    project_id: str
    series_id: str = ""
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
    status: Literal["published", "needs_review", "archived"] = "published"
    index_synced: bool = True


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
                warnings.insert(0, "目标飞书知识库不可用，正在使用最后确认的本地缓存；它是非权威版本。")
                return KnowledgeEvidenceBundle(
                    items=self.cached_bundle.items,
                    warnings=tuple(warnings),
                    offline=True,
                    fetched_at=self.cached_bundle.fetched_at,
                )
            raise error

        candidates = []
        for entry in index.values():
            parts = entry.key.split("/")
            if len(parts) != 3 or parts[1] != query.project_id:
                continue
            if query.series_id and parts[0] != query.series_id:
                continue
            if query.page_types and parts[2] not in query.page_types:
                continue
            candidates.append(entry)

        items = []
        warnings = []
        for entry in candidates:
            if entry.status == "archived":
                warnings.append(f"{entry.key} 已归档，不能作为权威知识")
                continue
            try:
                raw = self.cli.fetch_doc(entry.doc_token)
                document = raw.get("data", {}).get("document", {})
                page = parse_remote_page(document.get("content", ""))
                if (page.metadata["key"] != entry.key
                        or page.metadata["source_revisions"] != entry.source_revisions):
                    raise ValueError(
                        f"系统元数据与索引不一致：{entry.key}"
                    )
                if page.metadata["last_ai_revision_id"] != entry.last_ai_revision_id:
                    raise ValueError(f"页面与索引 last_ai_revision_id 不一致：{entry.key}")
                actual_revision = int(document["revision_id"])
                if actual_revision < entry.last_seen_revision_id:
                    raise ValueError(
                        f"页面 revision 落后于索引 last_seen_revision_id：{entry.key}"
                    )
                index_synced = actual_revision == entry.last_seen_revision_id
                if not index_synced:
                    warnings.append(f"{entry.key} 页面 revision 新于索引，索引尚未同步")
                score = sum(1 for term in query.terms if term in page.body)
                if query.terms and score == 0:
                    continue
                items.append((score, entry, page, document, index_synced))
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
                title=entry.key,
                content=page.body,
                source_revisions=dict(entry.source_revisions),
                status=entry.status,
                index_synced=index_synced,
            )
            for score, entry, page, document, index_synced in items[:query.limit]
        )

        bundle = KnowledgeEvidenceBundle(
            items=evidence,
            warnings=tuple(warnings),
            offline=False,
            fetched_at=_now(),
        )
        if (
            self.cache_store is not None
            and all(item.index_synced for item in evidence)
        ):
            self.cache_store.save(bundle_to_dict(bundle))
        return bundle


def bundle_to_dict(bundle: KnowledgeEvidenceBundle) -> dict[str, Any]:
    return {
        "items": [
            {
                "key": item.key,
                "doc_token": item.doc_token,
                "revision_id": item.revision_id,
                "title": item.title,
                "content": item.content,
                "source_revisions": item.source_revisions,
                "status": item.status,
                "index_synced": item.index_synced,
            }
            for item in bundle.items
        ],
        "warnings": list(bundle.warnings),
        "offline": bundle.offline,
        "fetched_at": bundle.fetched_at,
    }


def bundle_from_dict(raw: dict[str, Any] | None) -> KnowledgeEvidenceBundle | None:
    if raw is None:
        return None
    return KnowledgeEvidenceBundle(
        items=tuple(KnowledgeEvidence(**item) for item in raw.get("items", [])),
        warnings=tuple(raw.get("warnings", [])),
        offline=bool(raw.get("offline", False)),
        fetched_at=raw["fetched_at"],
    )


def _now() -> str:
    return datetime.now(timezone(timedelta(hours=8))).isoformat()


if __name__ == "__main__":
    raise SystemExit("use skills/knowledge-loader/SKILL.md, not this module directly")
