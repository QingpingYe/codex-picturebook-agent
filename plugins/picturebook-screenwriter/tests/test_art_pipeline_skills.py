import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
PROMPT_SKILL = ROOT / "skills" / "image-prompt-architect" / "SKILL.md"
GENERATE_SKILL = ROOT / "skills" / "image-generate" / "SKILL.md"


class PromptArchitectTests(unittest.TestCase):
    def test_skill_outputs_structured_json_only(self):
        text = PROMPT_SKILL.read_text(encoding="utf-8")
        self.assertIn("结构化 JSON", text)
        self.assertIn("不生成图片", text)
        self.assertIn("frame_laws", text)

    def test_generation_requires_explicit_user_choices(self):
        text = GENERATE_SKILL.read_text(encoding="utf-8")
        self.assertIn("明确确认", text)
        self.assertIn("输出目录", text)
        self.assertIn("不得默认", text)


if __name__ == "__main__":
    unittest.main()
