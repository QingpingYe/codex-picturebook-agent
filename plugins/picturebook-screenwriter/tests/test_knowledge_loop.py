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
