import json
import unittest
from dependencies import (
    build_dependency_record,
    find_stale_dependencies,
    parse_dependency_record,
    render_dependency_record,
)
from load_knowledge import KnowledgeEvidence, KnowledgeEvidenceBundle
from models import IndexEntry


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

    def test_dataclass_bundle_round_trip(self):
        bundle = KnowledgeEvidenceBundle(
            items=(
                KnowledgeEvidence(
                    key="海外绘本/小老鼠迈尔斯/worldview",
                    doc_token="doc-a",
                    revision_id=42,
                    title="worldview",
                    content="# 世界观\n\n小老鼠迈尔斯住在森林里。",
                    source_revisions={"node-a": "17"},
                ),
            ),
            warnings=(),
            offline=False,
            fetched_at="2026-09-18T10:00:00+08:00",
        )
        record = build_dependency_record(bundle, "demo-script-v1", "script")
        rendered = render_dependency_record(record)
        parsed = parse_dependency_record(rendered)
        self.assertEqual(parsed, record)

    def test_realistic_saved_artifact_round_trip(self):
        record = build_dependency_record(BUNDLE, "demo-script-v1", "script")
        artifact = "# 草稿标题\n\n迈尔斯住在一座蓝色树屋里。\n\n" + render_dependency_record(record)
        parsed = parse_dependency_record(artifact)
        self.assertEqual(parsed, record)

    def test_index_entry_revision_change_is_stale(self):
        record = build_dependency_record(BUNDLE, "demo-script-v1", "script")
        index = {
            "海外绘本/小老鼠迈尔斯/worldview": IndexEntry(
                key="海外绘本/小老鼠迈尔斯/worldview",
                doc_token="doc-a",
                wiki_node_token="node-a",
                source_revisions={"node-a": "17"},
                last_ai_revision_id=42,
                last_seen_revision_id=45,
                status="published",
            )
        }
        stale = find_stale_dependencies(record, index)
        self.assertEqual(len(stale), 1)
        self.assertEqual(stale[0].reason, "revision_changed")
        self.assertEqual(stale[0].current_revision_id, 45)

    def test_unchanged_index_entry_is_current_with_fetched_bundle(self):
        bundle = KnowledgeEvidenceBundle(
            items=(KnowledgeEvidence(
                key="海外绘本/小老鼠迈尔斯/worldview",
                doc_token="doc-a",
                revision_id=42,
                title="worldview",
                content="# 世界观",
                source_revisions={"node-a": "17"},
            ),),
            warnings=(),
            offline=False,
            fetched_at="2026-09-18T10:00:00+08:00",
        )
        record = build_dependency_record(bundle, "demo-script-v1", "script")
        index = {
            "海外绘本/小老鼠迈尔斯/worldview": IndexEntry(
                key="海外绘本/小老鼠迈尔斯/worldview",
                doc_token="doc-a",
                wiki_node_token="node-a",
                source_revisions={"node-a": "17"},
                last_ai_revision_id=42,
                last_seen_revision_id=42,
                status="published",
            )
        }
        self.assertEqual(find_stale_dependencies(record, index, bundle), ())

    def test_current_bundle_with_unsynced_index_is_stale(self):
        record = build_dependency_record(BUNDLE, "demo-script-v1", "script")
        current_bundle = KnowledgeEvidenceBundle(
            items=(KnowledgeEvidence(
                key="海外绘本/小老鼠迈尔斯/worldview",
                doc_token="doc-a",
                revision_id=42,
                title="worldview",
                content="# 世界观",
                source_revisions={"node-a": "17"},
                index_synced=False,
            ),),
            warnings=("索引尚未同步",),
            offline=False,
            fetched_at="2026-09-22T10:00:00+08:00",
        )
        index = {
            "海外绘本/小老鼠迈尔斯/worldview": IndexEntry(
                key="海外绘本/小老鼠迈尔斯/worldview",
                doc_token="doc-a",
                wiki_node_token="node-a",
                source_revisions={"node-a": "17"},
                last_ai_revision_id=42,
                last_seen_revision_id=42,
                status="published",
            )
        }

        stale = find_stale_dependencies(record, index, current_bundle)

        self.assertEqual(len(stale), 1)
        self.assertEqual(stale[0].reason, "index_unsynced")

    def test_fetched_revision_wins_when_index_is_not_refreshed(self):
        old_record = build_dependency_record(BUNDLE, "demo-script-v1", "script")
        current_bundle = KnowledgeEvidenceBundle(
            items=(KnowledgeEvidence(
                key="海外绘本/小老鼠迈尔斯/worldview",
                doc_token="doc-a",
                revision_id=45,
                title="worldview",
                content="# 新世界观",
                source_revisions={"node-a": "17"},
            ),),
            warnings=(),
            offline=False,
            fetched_at="2026-09-18T11:00:00+08:00",
        )
        stale_index = {
            "海外绘本/小老鼠迈尔斯/worldview": {
                "revision_id": 42,
                "status": "published",
            }
        }
        stale = find_stale_dependencies(old_record, stale_index, current_bundle)
        self.assertEqual(stale[0].reason, "revision_changed")
        self.assertEqual(stale[0].current_revision_id, 45)

    def test_index_only_check_never_claims_current(self):
        record = build_dependency_record(BUNDLE, "demo-script-v1", "script")
        index = {
            "海外绘本/小老鼠迈尔斯/worldview": {
                "revision_id": 42,
                "status": "published",
            }
        }
        stale = find_stale_dependencies(record, index)
        self.assertEqual(stale[0].reason, "index_unverified")

    def test_archived_index_entry_is_stale(self):
        record = build_dependency_record(BUNDLE, "demo-script-v1", "script")
        index = {
            "海外绘本/小老鼠迈尔斯/worldview": {
                "revision_id": 42,
                "status": "archived",
            }
        }
        stale = find_stale_dependencies(record, index)
        self.assertEqual(stale[0].reason, "archived")

    def test_all_advertised_artifact_types_are_accepted(self):
        for artifact_type in ("positioning", "topic_plan", "worldview", "characters", "outline", "script"):
            with self.subTest(artifact_type=artifact_type):
                record = build_dependency_record(BUNDLE, f"demo-{artifact_type}-v1", artifact_type)
                self.assertEqual(record.artifact_type, artifact_type)

    def test_build_rejects_invalid_required_evidence_fields(self):
        for missing in ("key", "doc_token", "revision_id", "source_revisions"):
            with self.subTest(missing=missing):
                invalid_item = dict(BUNDLE["items"][0])
                invalid_item.pop(missing)
                bundle = {**BUNDLE, "items": [invalid_item]}
                with self.assertRaisesRegex(ValueError, "依赖证据字段无效"):
                    build_dependency_record(bundle, "demo-v1", "script")

    def test_build_rejects_wrong_evidence_field_types(self):
        cases = (
            {**BUNDLE["items"][0], "key": 42},
            {**BUNDLE["items"][0], "doc_token": None},
            {**BUNDLE["items"][0], "revision_id": True},
            {**BUNDLE["items"][0], "source_revisions": ["node-a"]},
            {**BUNDLE["items"][0], "source_revisions": {"node-a": 17}},
        )
        for item in cases:
            with self.subTest(item=item):
                bundle = {**BUNDLE, "items": [item]}
                with self.assertRaisesRegex(ValueError, "依赖证据字段无效"):
                    build_dependency_record(bundle, "demo-v1", "script")

    def test_parse_rejects_invalid_required_evidence_fields(self):
        required = ("key", "doc_token", "revision_id", "source_revisions")
        for missing in required:
            with self.subTest(missing=missing):
                payload = {**BUNDLE["items"][0]}
                payload.pop(missing)
                envelope = (
                    "## built_against\n```json\n"
                    + json.dumps({
                        "artifact_id": "demo-v1",
                        "artifact_type": "script",
                        "built_at": BUNDLE["fetched_at"],
                        "evidence": [payload],
                    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                    + "\n```\n"
                )
                with self.assertRaisesRegex(ValueError, "依赖证据字段无效"):
                    parse_dependency_record(envelope)

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


if __name__ == "__main__":
    unittest.main()
