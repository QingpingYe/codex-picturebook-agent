import unittest
from dependencies import (
    build_dependency_record,
    find_stale_dependencies,
    parse_dependency_record,
    render_dependency_record,
)
from load_knowledge import KnowledgeEvidence, KnowledgeEvidenceBundle


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
