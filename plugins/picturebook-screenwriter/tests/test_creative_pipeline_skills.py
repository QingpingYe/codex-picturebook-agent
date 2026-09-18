import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
STORY = ROOT / "skills" / "story-planning" / "SKILL.md"
PRE_CREATE = ROOT / "skills" / "pre_create-baseline" / "SKILL.md"
IN_CREATE = ROOT / "skills" / "in_create-baseline" / "SKILL.md"
POST_CREATE = ROOT / "skills" / "post_create-baseline" / "SKILL.md"


class StoryPlanningTests(unittest.TestCase):
    def test_explicit_lightweight_trigger_only(self):
        text = STORY.read_text(encoding="utf-8")
        self.assertIn("显式", text)
        self.assertIn("轻量", text)
        self.assertIn("不落盘", text)


class PreCreateBaselineTests(unittest.TestCase):
    def test_pre_create_reports_gaps_not_defaults(self):
        text = PRE_CREATE.read_text(encoding="utf-8")
        self.assertIn("缺口", text)
        self.assertIn("不猜默认值", text)


class InCreateBaselineTests(unittest.TestCase):
    def test_in_create_requires_structure_before_body(self):
        text = IN_CREATE.read_text(encoding="utf-8")
        self.assertIn("结构骨架", text)
        self.assertIn("边界卡", text)
        self.assertIn("text-craft", text)


class PostCreateBaselineTests(unittest.TestCase):
    def test_post_create_is_polish_not_rewrite(self):
        text = POST_CREATE.read_text(encoding="utf-8")
        self.assertIn("只做打磨", text)
        self.assertIn("不得新增", text)


if __name__ == "__main__":
    unittest.main()
