import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from shared_schema import (
    SchemaError,
    logical_key,
    normalize_project_id,
    normalize_series_id,
    validate_revisions,
)


class SharedSchemaTests(unittest.TestCase):
    def test_common_page_project_id_is_common(self):
        self.assertEqual(normalize_project_id("ip-overview", ""), "common")

    def test_creation_standards_is_dual_scope(self):
        self.assertEqual(normalize_project_id("creation-standards", "common"), "common")
        self.assertEqual(normalize_project_id("creation-standards", "小老鼠迈尔斯"), "小老鼠迈尔斯")

    def test_creation_standards_requires_project_id(self):
        with self.assertRaises(SchemaError):
            normalize_project_id("creation-standards", "")

    def test_creation_standards_logical_keys_at_both_levels(self):
        self.assertEqual(
            logical_key("海外绘本", "common", "creation-standards"),
            "海外绘本/common/creation-standards",
        )
        self.assertEqual(
            logical_key("海外绘本", "小老鼠迈尔斯", "creation-standards"),
            "海外绘本/小老鼠迈尔斯/creation-standards",
        )

    def test_project_page_project_id_is_preserved(self):
        self.assertEqual(normalize_project_id("worldview", "小老鼠迈尔斯"), "小老鼠迈尔斯")

    def test_system_page_ids_are_system(self):
        self.assertEqual(normalize_series_id("index", ""), "system")
        self.assertEqual(normalize_project_id("index", ""), "system")

    def test_revision_must_be_text(self):
        self.assertEqual(validate_revisions(["18"], ["18"]), {"18": "18"})

    def test_token_and_revision_must_match(self):
        with self.assertRaises(SchemaError):
            validate_revisions(["a", "b"], ["1"])

    def test_logical_key_has_three_nonempty_segments(self):
        key = logical_key("海外绘本", "小老鼠迈尔斯", "worldview")
        self.assertEqual(key, "海外绘本/小老鼠迈尔斯/worldview")

    def test_empty_project_key_is_rejected(self):
        with self.assertRaises(SchemaError):
            logical_key("海外绘本", "", "worldview")


if __name__ == "__main__":
    unittest.main()
