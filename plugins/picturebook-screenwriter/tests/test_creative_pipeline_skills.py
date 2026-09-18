import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
STORY = ROOT / "skills" / "story-planning" / "SKILL.md"


class StoryPlanningTests(unittest.TestCase):
    def test_explicit_lightweight_trigger_only(self):
        text = STORY.read_text(encoding="utf-8")
        self.assertIn("显式", text)
        self.assertIn("轻量", text)
        self.assertIn("不落盘", text)


if __name__ == "__main__":
    unittest.main()
