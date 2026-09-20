import json
import tempfile
import unittest
from pathlib import Path

from export_session import build_manifest, export_session


class ExportSessionTests(unittest.TestCase):
    def test_manifest_counts_inputs_without_adding_content(self):
        manifest = build_manifest(
            "s1",
            [{"role": "user"}],
            ["picturebook/script_v1.md"],
            ["p1.png"],
        )
        self.assertEqual(
            manifest["summary"],
            {"messages": 1, "files": 1, "images": 1},
        )

    def test_output_dir_must_be_absolute(self):
        with self.assertRaisesRegex(ValueError, "absolute"):
            export_session("s1", [], [], [], Path("exports"), Path.cwd())

    def test_rejects_output_inside_plugin(self):
        with tempfile.TemporaryDirectory() as temp:
            plugin_root = Path(temp) / "plugin"
            plugin_root.mkdir()
            with self.assertRaisesRegex(ValueError, "outside the plugin"):
                export_session("s1", [], [], [], plugin_root, plugin_root)

    def test_rejects_unsafe_session_id(self):
        with tempfile.TemporaryDirectory() as temp:
            output_dir = Path(temp)
            with self.assertRaisesRegex(ValueError, "session id"):
                export_session(
                    "../outside",
                    [],
                    [],
                    [],
                    output_dir,
                    Path(temp) / "plugin",
                )

    def test_rejects_secret_fields(self):
        with tempfile.TemporaryDirectory() as temp:
            messages = [{"metadata": {"api_key": "not-for-export"}}]
            with self.assertRaisesRegex(ValueError, "secret field"):
                export_session(
                    "s1",
                    messages,
                    [],
                    [],
                    Path(temp),
                    Path(temp) / "plugin",
                )

    def test_writes_explicit_bundle(self):
        with tempfile.TemporaryDirectory() as temp:
            output_dir = Path(temp)
            plugin_root = output_dir / "plugin"
            plugin_root.mkdir()
            bundle = export_session(
                "s1",
                [{"role": "user"}],
                ["picturebook/script_v1.md"],
                ["p1.png"],
                output_dir,
                plugin_root,
            )
            manifest = json.loads(
                (bundle / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["summary"]["messages"], 1)
            self.assertEqual(
                json.loads((bundle / "files.json").read_text(encoding="utf-8")),
                ["picturebook/script_v1.md"],
            )


if __name__ == "__main__":
    unittest.main()
