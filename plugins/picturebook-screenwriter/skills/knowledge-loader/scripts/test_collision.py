import unittest
from load_knowledge import KnowledgeEvidence, KnowledgeEvidenceBundle

from collision import check_collisions


class CollisionTests(unittest.TestCase):
    def test_shared_character_name_is_reported(self):
        bundle = {"items": [{"key": "series/common/characters", "content": "迈尔斯是一只小老鼠。"}]}
        hits = check_collisions("迈尔斯开始冒险。", bundle, ("迈尔斯",))
        self.assertEqual(len(hits), 1)
        self.assertIn("迈尔斯", hits[0].evidence_excerpt)

    def test_authority_bundle_can_be_checked_directly(self):
        bundle = KnowledgeEvidenceBundle(
            items=(
                KnowledgeEvidence(
                    key="series/common/characters",
                    doc_token="doc-a",
                    revision_id=42,
                    title="characters",
                    content="迈尔斯是一只小老鼠。",
                    source_revisions={"node-a": "17"},
                ),
            ),
            warnings=(),
            offline=False,
            fetched_at="2026-09-18T10:00:00+08:00",
        )
        hits = check_collisions("迈尔斯开始冒险。", bundle, ("迈尔斯",))
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].key, "series/common/characters")


if __name__ == "__main__":
    unittest.main()
