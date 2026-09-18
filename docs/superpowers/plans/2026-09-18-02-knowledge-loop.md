# Knowledge Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn authoritative Feishu retrieval into a real creation dependency with evidence, locking, collision checks, and stale propagation.

**Architecture:** Extend `knowledge-loader` with a dependency module that converts evidence bundles into `built_against` records, checks new drafts for conflicts, and compares current Wiki revisions against locked dependencies. The entry skill will refuse to treat stale knowledge as authoritative without warning, and every report will remain machine-readable.

**Tech Stack:** Python 3.10+ standard library, `unittest`, Feishu `docx` revision IDs, existing `ControlPlane` and `KnowledgeLoader`.

**Spec:** `docs/superpowers/specs/2026-09-18-workbuddy-codex-plugin-roadmap.md` and `docs/superpowers/specs/2026-09-17-feishu-authoritative-knowledge-base-design.md`

## Global Constraints

- The target Feishu Wiki is the only shared authority.
- Original Wiki is read-only.
- Every evidence item must retain `key`, `doc_token`, `revision_id`, and `source_revisions`.
- `built_against` is local artifact metadata, never shared authority.
- Human edits always win; collision reports are advisory to the user, not automatic rewrites.
- A stale artifact is still readable, but must not be presented as current.
- Offline tests must use fake control planes and fake CLI clients.

---

### Task 1: Add a dependency metadata module

**Files:**
- Create: `plugins/picturebook-screenwriter/skills/knowledge-loader/scripts/dependencies.py`
- Test: `plugins/picturebook-screenwriter/skills/knowledge-loader/scripts/test_dependencies.py`

**Interfaces:**
- Consumes: `KnowledgeEvidenceBundle`, `KnowledgeEvidence`.
- Produces:
  - `build_dependency_record(bundle, artifact_id, artifact_type) -> DependencyRecord`
  - `render_dependency_record(record) -> str`
  - `parse_dependency_record(markdown) -> DependencyRecord`

- [ ] **Step 1: Write the failing tests**

```python
import unittest
from dependencies import build_dependency_record, render_dependency_record, parse_dependency_record


BUNDLE = {
    "items": [
        {
            "key": "海外绘本/小老鼠迈尔斯/worldview",
            "doc_token": "doc-a",
            "revision_id": 42,
            "source_revisions": {"node-a": "17"},
        }
    ],
    "warnings": [],
    "offline": False,
    "fetched_at": "2026-09-18T10:00:00+08:00",
}


class DependencyTests(unittest.TestCase):
    def test_round_trip(self):
        record = build_dependency_record(BUNDLE, "demo-script-v1", "script")
        rendered = render_dependency_record(record)
        parsed = parse_dependency_record(rendered)
        self.assertEqual(parsed, record)

    def test_offline_bundle_cannot_create_authoritative_record(self):
        offline = {**BUNDLE, "offline": True}
        with self.assertRaisesRegex(ValueError, "离线缓存"):
            build_dependency_record(offline, "demo-script-v1", "script")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run and verify failure**

```powershell
python .\plugins\picturebook-screenwriter\skills\knowledge-loader\scripts\test_dependencies.py -v
```

Expected: `ModuleNotFoundError: No module named 'dependencies'`.

- [ ] **Step 3: Implement the module**

```python
"""Creation dependency locking for authoritative Feishu evidence."""

import json
from dataclasses import asdict, dataclass, is_dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class DependencyRecord:
    artifact_id: str
    artifact_type: str
    built_at: str
    evidence: tuple[Mapping[str, Any], ...]


def build_dependency_record(bundle: Mapping[str, Any], artifact_id: str,
                            artifact_type: str) -> DependencyRecord:
    if _bundle_offline(bundle):
        raise ValueError("离线缓存不能生成权威依赖记录")
    if not artifact_id or artifact_type not in {
        "positioning", "topic", "worldview", "character", "outline", "script"
    }:
        raise ValueError("invalid artifact id or type")
    items = tuple(_bundle_items(bundle))
    if not items:
        raise ValueError("knowledge bundle has no evidence")
    return DependencyRecord(
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        built_at=bundle["fetched_at"],
        evidence=items,
    )


def _bundle_offline(bundle: Any) -> bool:
    return bool(bundle.get("offline")) if isinstance(bundle, Mapping) else bool(bundle.offline)


def _bundle_items(bundle: Any) -> tuple[dict[str, Any], ...]:
    if isinstance(bundle, Mapping):
        raw_items = bundle.get("items", ())
    else:
        raw_items = bundle.items
    return tuple(asdict(item) if is_dataclass(item) else dict(item) for item in raw_items)


def render_dependency_record(record: DependencyRecord) -> str:
    payload = {
        "artifact_id": record.artifact_id,
        "artifact_type": record.artifact_type,
        "built_at": record.built_at,
        "evidence": [dict(item) for item in record.evidence],
    }
    return "## built_against\n```json\n" + json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ) + "\n```\n"


def parse_dependency_record(markdown: str) -> DependencyRecord:
    prefix = "## built_against\n```json\n"
    suffix = "\n```\n"
    if not markdown.startswith(prefix) or not markdown.endswith(suffix):
        raise ValueError("invalid built_against envelope")
    payload = json.loads(markdown[len(prefix):-len(suffix)])
    return DependencyRecord(
        artifact_id=payload["artifact_id"],
        artifact_type=payload["artifact_type"],
        built_at=payload["built_at"],
        evidence=tuple(payload["evidence"]),
    )
```

- [ ] **Step 4: Run and verify success**

```powershell
python .\plugins\picturebook-screenwriter\skills\knowledge-loader\scripts\test_dependencies.py -v
```

Expected: 2 tests pass.

- [ ] **Step 5: Commit**

```powershell
git add .\plugins\picturebook-screenwriter\skills\knowledge-loader\scripts\dependencies.py .\plugins\picturebook-screenwriter\skills\knowledge-loader\scripts\test_dependencies.py
git commit -m "feat: lock creation knowledge dependencies"
```

---

### Task 2: Add stale propagation

**Files:**
- Modify: `plugins/picturebook-screenwriter/skills/knowledge-loader/scripts/dependencies.py`
- Test: `plugins/picturebook-screenwriter/skills/knowledge-loader/scripts/test_dependencies.py`

**Interfaces:**
- Produces:
  - `StaleReason` dataclass with `key`, `locked_revision_id`, `current_revision_id`, `status`.
  - `find_stale_dependencies(record, current_index) -> tuple[StaleReason, ...]`

- [ ] **Step 1: Add failing stale tests**

```python
from dependencies import find_stale_dependencies

    def test_revision_change_is_stale(self):
        record = build_dependency_record(BUNDLE, "demo-script-v1", "script")
        current = {
            "海外绘本/小老鼠迈尔斯/worldview": {
                "key": "海外绘本/小老鼠迈尔斯/worldview",
                "doc_token": "doc-a",
                "revision_id": 45,
                "status": "published",
            }
        }
        stale = find_stale_dependencies(record, current)
        self.assertEqual(len(stale), 1)
        self.assertEqual(stale[0].current_revision_id, 45)

    def test_missing_key_is_stale(self):
        record = build_dependency_record(BUNDLE, "demo-script-v1", "script")
        stale = find_stale_dependencies(record, {})
        self.assertEqual(stale[0].reason, "missing")
```

- [ ] **Step 2: Implement stale checking**

```python
@dataclass(frozen=True)
class StaleReason:
    key: str
    reason: str
    locked_revision_id: int | None
    current_revision_id: int | None
    status: str | None


def find_stale_dependencies(record: DependencyRecord,
                            current_index: Mapping[str, Mapping[str, Any]]) -> tuple[StaleReason, ...]:
    stale: list[StaleReason] = []
    for item in record.evidence:
        key = item["key"]
        current = current_index.get(key)
        if current is None:
            stale.append(StaleReason(key, "missing", item["revision_id"], None, None))
            continue
        if current.get("revision_id") != item["revision_id"]:
            stale.append(StaleReason(
                key, "revision_changed", item["revision_id"],
                current.get("revision_id"), current.get("status"),
            ))
        if current.get("status") == "needs_review":
            stale.append(StaleReason(
                key, "needs_review", item["revision_id"],
                current.get("revision_id"), "needs_review",
            ))
    return tuple(stale)
```

- [ ] **Step 3: Run the tests**

```powershell
python .\plugins\picturebook-screenwriter\skills\knowledge-loader\scripts\test_dependencies.py -v
```

Expected: 4 tests pass.

- [ ] **Step 4: Commit**

```powershell
git add .\plugins\picturebook-screenwriter\skills\knowledge-loader\scripts\dependencies.py .\plugins\picturebook-screenwriter\skills\knowledge-loader\scripts\test_dependencies.py
git commit -m "feat: propagate stale knowledge dependencies"
```

---

### Task 3: Add authority loading with required page types

**Files:**
- Create: `plugins/picturebook-screenwriter/skills/knowledge-loader/scripts/authority.py`
- Test: `plugins/picturebook-screenwriter/skills/knowledge-loader/scripts/test_authority.py`

**Interfaces:**
- Produces:
  - `AuthorityQuery(project_id, series_id, page_types)`
  - `AuthorityLoader.load(query) -> KnowledgeEvidenceBundle`
  - Raises `AuthorityGapError` with missing keys.

- [ ] **Step 1: Add failing authority tests**

```python
import unittest
from types import SimpleNamespace
from authority import AuthorityLoader, AuthorityQuery, AuthorityGapError


class FakeControlPlane:
    def read_index(self):
        return {
            "海外绘本/小老鼠迈尔斯/worldview": SimpleNamespace(
                key="海外绘本/小老鼠迈尔斯/worldview",
                doc_token="doc-a",
                wiki_node_token="wiki-a",
                source_revisions={"node-a": "17"},
                last_ai_revision_id=42,
                last_seen_revision_id=42,
                status="published",
            )
        }


class FakeCli:
    def fetch_doc(self, token):
        return {"data": {"document": {
            "revision_id": 42,
            "content": "# 世界观\n\n正文\n\n## 系统元数据（请勿编辑）\n```json\n{\"schema_version\":1,\"key\":\"海外绘本/小老鼠迈尔斯/worldview\",\"page_type\":\"worldview\",\"source_node_tokens\":[\"node-a\"],\"source_revisions\":{\"node-a\":\"17\"},\"last_ai_revision_id\":42}\n```\n",
        }}}


class AuthorityTests(unittest.TestCase):
    def test_missing_required_page_is_blocking(self):
        loader = AuthorityLoader(FakeControlPlane(), FakeCli())
        query = AuthorityQuery("小老鼠迈尔斯", "海外绘本", ("worldview", "characters"))
        with self.assertRaisesRegex(AuthorityGapError, "characters"):
            loader.load(query)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Implement authority loading**

```python
"""Full authority load for project creation dependencies."""

from dataclasses import dataclass

from load_knowledge import KnowledgeLoader, KnowledgeQuery


class AuthorityGapError(RuntimeError):
    pass


@dataclass(frozen=True)
class AuthorityQuery:
    project_id: str
    series_id: str
    page_types: tuple[str, ...]


class AuthorityLoader:
    def __init__(self, control_plane, cli) -> None:
        self.loader = KnowledgeLoader(control_plane, cli)

    def load(self, query: AuthorityQuery):
        bundle = self.loader.load(KnowledgeQuery(
            project_id=query.project_id,
            page_types=query.page_types,
            limit=len(query.page_types) * 2,
        ))
        found = {item.key.split("/")[-1] for item in bundle.items}
        missing = [page_type for page_type in query.page_types if page_type not in found]
        if missing:
            raise AuthorityGapError("缺少权威知识页：" + "、".join(missing))
        return bundle
```

- [ ] **Step 3: Run tests**

```powershell
python .\plugins\picturebook-screenwriter\skills\knowledge-loader\scripts\test_authority.py -v
```

Expected: 1 test passes.

- [ ] **Step 4: Commit**

```powershell
git add .\plugins\picturebook-screenwriter\skills\knowledge-loader\scripts\authority.py .\plugins\picturebook-screenwriter\skills\knowledge-loader\scripts\test_authority.py
git commit -m "feat: add blocking authority loader"
```

---

### Task 4: Add deterministic collision checks

**Files:**
- Create: `plugins/picturebook-screenwriter/skills/knowledge-loader/scripts/collision.py`
- Test: `plugins/picturebook-screenwriter/skills\knowledge-loader\scripts\test_collision.py`

**Interfaces:**
- Produces:
  - `CollisionHit(key, term, evidence_excerpt, draft_excerpt)`
  - `check_collisions(draft, bundle, terms) -> tuple[CollisionHit, ...]`

- [ ] **Step 1: Add failing collision tests**

```python
import unittest
from collision import check_collisions


class CollisionTests(unittest.TestCase):
    def test_shared_character_name_is_reported(self):
        bundle = {"items": [{"key": "series/common/characters", "content": "迈尔斯是一只小老鼠。"}]}
        hits = check_collisions("迈尔斯开始冒险。", bundle, ("迈尔斯",))
        self.assertEqual(len(hits), 1)
        self.assertIn("迈尔斯", hits[0].evidence_excerpt)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Implement collision checks**

```python
"""Cheap, deterministic collision scan between a draft and evidence."""

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class CollisionHit:
    key: str
    term: str
    evidence_excerpt: str
    draft_excerpt: str


def check_collisions(draft: str, bundle: Mapping[str, object],
                     terms: tuple[str, ...]) -> tuple[CollisionHit, ...]:
    hits: list[CollisionHit] = []
    for item in bundle["items"]:
        content = item["content"]
        for term in terms:
            if term in draft and term in content:
                hits.append(CollisionHit(
                    key=item["key"],
                    term=term,
                    evidence_excerpt=_excerpt(content, term),
                    draft_excerpt=_excerpt(draft, term),
                ))
    return tuple(hits)


def _excerpt(text: str, term: str, radius: int = 24) -> str:
    index = text.find(term)
    start = max(0, index - radius)
    end = min(len(text), index + len(term) + radius)
    return text[start:end].replace("\n", " ")
```

- [ ] **Step 3: Run tests**

```powershell
python .\plugins\picturebook-screenwriter\skills\knowledge-loader\scripts\test_collision.py -v
```

Expected: 1 test passes.

- [ ] **Step 4: Commit**

```powershell
git add .\plugins\picturebook-screenwriter\skills\knowledge-loader\scripts\collision.py .\plugins\picturebook-screenwriter\skills\knowledge-loader\scripts\test_collision.py
git commit -m "feat: add knowledge collision checks"
```

---

### Task 5: Integrate the knowledge loop into the entry skill

**Files:**
- Modify: `plugins/picturebook-screenwriter/skills/picturebook-screenwriter/SKILL.md`
- Modify: `plugins/picturebook-screenwriter/tests/test_skill_contract.py`

**Interfaces:**
- Consumes: `AuthorityLoader`, `build_dependency_record`, `check_collisions`, `find_stale_dependencies`.
- Produces: an entry workflow that creates, stores, and checks `built_against`.

- [ ] **Step 1: Add contract tests**

```python
    def test_entry_records_built_against(self):
        text = ENTRY_SKILL.read_text(encoding="utf-8")
        self.assertIn("built_against", text)
        self.assertIn("权威知识缺失", text)

    def test_entry_warns_when_knowledge_is_stale(self):
        text = ENTRY_SKILL.read_text(encoding="utf-8")
        self.assertIn("陈旧", text)
        self.assertIn("revision_id", text)
```

- [ ] **Step 2: Update the workflow**

Add to the entry skill:

```markdown
## Knowledge Dependency Gate

创作或修订前必须装载目标项目的权威知识。若必需页面缺失，明确报告“权威知识缺失”，不得用本地缓存或猜测内容替代。草稿获得用户批准并落盘前，生成 `built_against` 元数据，记录每个引用知识页的 `key`、`doc_token`、`revision_id` 和 `source_revisions`。

后续读取旧产物时，先比对当前飞书索引。只要 `revision_id` 变化、条目缺失或状态为 `needs_review`，必须在回复首段标明“知识已陈旧”，列出差异，并询问是否基于当前权威知识修订。不得把陈旧产物描述为最新定稿。
```

- [ ] **Step 3: Run tests**

```powershell
python .\plugins\picturebook-screenwriter\tests\test_skill_contract.py -v
python .\plugins\picturebook-screenwriter\skills\knowledge-loader\scripts\test_dependencies.py -v
```

Expected: all tests pass.

- [ ] **Step 4: Commit**

```powershell
git add .\plugins\picturebook-screenwriter\skills\picturebook-screenwriter\SKILL.md .\plugins\picturebook-screenwriter\tests\test_skill_contract.py
git commit -m "feat: integrate knowledge dependency gate"
```

---

### Task 6: Add offline end-to-end knowledge loop coverage

**Files:**
- Create: `plugins/picturebook-screenwriter/tests/test_knowledge_loop.py`

**Interfaces:**
- Consumes: all Phase 2 modules.
- Produces: a regression test proving authority load, dependency lock, collision report, and stale propagation work together.

- [ ] **Step 1: Write the end-to-end test**

```python
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).parents[1] / "skills" / "knowledge-loader" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from dependencies import build_dependency_record, find_stale_dependencies
from collision import check_collisions


BUNDLE = {
    "items": [{
        "key": "海外绘本/小老鼠迈尔斯/worldview",
        "doc_token": "doc-a",
        "revision_id": 42,
        "content": "迈尔斯住在一座蓝色树屋里。",
        "source_revisions": {"node-a": "17"},
    }],
    "warnings": (),
    "offline": False,
    "fetched_at": "2026-09-18T10:00:00+08:00",
}


class KnowledgeLoopTests(unittest.TestCase):
    def test_lock_collision_and_stale(self):
        record = build_dependency_record(BUNDLE, "demo-script-v1", "script")
        hits = check_collisions("迈尔斯住在红色火车里。", BUNDLE, ("迈尔斯", "蓝色树屋"))
        self.assertTrue(hits)
        stale = find_stale_dependencies(record, {
            "海外绘本/小老鼠迈尔斯/worldview": {
                "key": "海外绘本/小老鼠迈尔斯/worldview",
                "revision_id": 43,
                "status": "published",
            }
        })
        self.assertEqual(stale[0].reason, "revision_changed")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the complete Phase 2 suite**

```powershell
python .\plugins\picturebook-screenwriter\tests\test_knowledge_loop.py -v
python -m unittest discover -s .\plugins\picturebook-screenwriter\skills\knowledge-loader\scripts -p "test_*.py" -v
```

Expected: all tests pass.

- [ ] **Step 3: Commit**

```powershell
git add .\plugins\picturebook-screenwriter\tests\test_knowledge_loop.py
git commit -m "test: cover knowledge dependency loop"
```

---

## Self-Review

- Covers authority load, dependency lock, collision reporting, stale propagation, and entry integration.
- No task writes Feishu; all live access remains read-only through existing loader contracts.
- The implementation reuses the current evidence bundle shape and extends it without breaking existing fields.
- All tests are offline and deterministic.
