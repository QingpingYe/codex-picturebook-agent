import base64
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("embed_images.py")
PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


try:
    import PIL
except ImportError:
    PIL = None


def run_cli(*args: str):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
    )


@unittest.skipUnless(PIL is not None, "Pillow is optional but required for this test")
class EmbedImagesTests(unittest.TestCase):
    def create_html(self, temp: Path) -> Path:
        image = temp / "p1.png"
        image.write_bytes(PNG_1PX)
        html = temp / "preview.html"
        html.write_text(
            "<script>var IMGS = {\"p1.png\": \"p1.png\"};</script>",
            encoding="utf-8",
        )
        return html

    def test_required_cli_arguments_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            html_path = self.create_html(Path(temp))
            result = run_cli(str(html_path))

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("--limit-mb", result.stderr)
            self.assertIn("--quality", result.stderr)

    def test_embeds_local_image_as_data_url(self):
        with tempfile.TemporaryDirectory() as temp:
            html_path = self.create_html(Path(temp))
            result = run_cli(str(html_path), "--limit-mb", "10", "--quality", "70")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("data:image/jpeg;base64,", html_path.read_text(encoding="utf-8"))

    def test_fails_closed_when_final_html_exceeds_limit(self):
        with tempfile.TemporaryDirectory() as temp:
            html_path = self.create_html(Path(temp))
            original = html_path.read_text(encoding="utf-8")
            result = run_cli(str(html_path), "--limit-mb", "0.000001", "--quality", "70")

            self.assertEqual(result.returncode, 2)
            self.assertEqual(html_path.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
