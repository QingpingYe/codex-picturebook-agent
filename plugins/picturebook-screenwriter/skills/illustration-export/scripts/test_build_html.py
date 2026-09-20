import tempfile
import unittest
from pathlib import Path

from build_html import build_html


def payload_with(image_path: str) -> dict:
    return {
        "project_metadata": {
            "project_id": "demo",
            "series_id": "s",
            "title": "标题",
            "episode": 1,
            "target_age": "3-6岁",
            "total_pages": 1,
            "generated_at": "2026-09-18T10:00:00+08:00",
        },
        "pages": [
            {
                "page_number": 1,
                "english_text": "Hello",
                "chinese_translation": "你好",
                "illustration_description": "全景",
                "image_path": image_path,
            }
        ],
    }


class BuildHtmlTests(unittest.TestCase):
    def test_minimal_payload_renders_header_and_storyboard(self):
        with tempfile.TemporaryDirectory() as temp:
            temp_dir = Path(temp)
            image = temp_dir / "p1.png"
            image.write_bytes(b"fake")

            result = build_html(payload_with(str(image)), temp_dir)
            html = Path(result.html_path).read_text(encoding="utf-8")

            self.assertIn("标题", html)
            self.assertIn("Hello", html)
            self.assertIn("绘本预览", html)
            self.assertIn("p1.png", html)
            self.assertIn("header", result.sections_rendered)
            self.assertIn("storyboard", result.sections_rendered)

    def test_missing_required_metadata_field_fails(self):
        payload = payload_with("p1.png")
        del payload["project_metadata"]["title"]

        with self.assertRaisesRegex(ValueError, "title"):
            build_html(payload, Path("."))


if __name__ == "__main__":
    unittest.main()
