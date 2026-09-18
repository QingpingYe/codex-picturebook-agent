import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
STORY = ROOT / "skills" / "story-planning" / "SKILL.md"
PRE_CREATE = ROOT / "skills" / "pre_create-baseline" / "SKILL.md"


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


if __name__ == "__main__":
    unittest.main()
