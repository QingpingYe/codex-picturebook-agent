import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from config import ConfigError, load_config


def config_json(values):
    defaults = {
        "schema_version": 2,
        "source": {
            "space_id": "7682720271706361023",
            "root_mode": "space",
            "wiki_url": "https://wcno1rbz0o8i.feishu.cn/wiki/OI9gwaRv8i3RwOkGBNnc7VHinDd",
        },
        "target": {
            "space_id": "7686313522543774944",
            "root_token": "root-token",
        },
        "identity": "user",
        "lock_ttl_minutes": 45,
    }
    defaults.update(values)
    return json.dumps(defaults)


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = SCRIPTS

    def load(self, contents, environ, which=None):
        with patch("config.Path.read_text", return_value=contents):
            arguments = (str(self.tmp / "config.json"), self.tmp, environ)
            return load_config(*arguments) if which is None else load_config(*arguments, which=which)

    def test_environment_is_used_without_explicit_config(self):
        path = self.tmp / "from-environment.json"
        with patch("config_paths.Path.is_file", return_value=True), \
             patch("config.Path.read_text", return_value=config_json({})):
            config = load_config(None, self.tmp, {"PICTUREBOOK_KB_CONFIG": str(path)})
        self.assertEqual(config.target.root_token, "root-token")
        self.assertEqual(config.source.root_mode, "space")

    def test_placeholder_target_token_is_rejected(self):
        placeholder = {
            "target": {
                "space_id": "7686313522543774944",
                "root_token": "REPLACE_WITH_TARGET_ROOT_TOKEN",
            }
        }
        with self.assertRaisesRegex(ConfigError, "placeholder"):
            self.load(config_json(placeholder), {})

    def test_bot_identity_is_rejected(self):
        with self.assertRaisesRegex(ConfigError, "identity must be 'user'"):
            self.load(config_json({"identity": "bot"}), {})

    def test_boolean_schema_version_is_rejected(self):
        with self.assertRaisesRegex(ConfigError, "schema_version must be 2"):
            self.load(config_json({"schema_version": True}), {})

    def test_v1_is_rejected_as_deprecated(self):
        v1 = {
            "schema_version": 1,
            "source_wiki_url": "https://example.feishu.cn/wiki/source",
            "target_root_token": "REPLACE_WITH_TARGET_ROOT_TOKEN",
            "identity": "user",
            "lock_ttl_minutes": 45,
        }
        with self.assertRaisesRegex(ConfigError, "deprecated"):
            self.load(json.dumps(v1), {})

    def test_rejects_unknown_and_missing_fields(self):
        with self.assertRaisesRegex(ConfigError, "unexpected"):
            self.load(config_json({"extra": True}), {})
        with self.assertRaisesRegex(ConfigError, "non-empty"):
            self.load(config_json({"source": {
                "space_id": "7682720271706361023",
                "root_mode": "space",
                "wiki_url": None,
            }}), {})

    def test_cli_candidates_follow_portable_order_without_duplicates(self):
        config = self.load(config_json({}), {"LARK_CLI_PATH": "lark-cli"}, which=lambda _: "lark-cli")
        self.assertEqual(config.cli_candidates[0], Path("lark-cli"))
        self.assertEqual(len(config.cli_candidates), 1)

    def test_config_cli_candidates_exclude_machine_drive_fallback(self):
        config = self.load(config_json({}), {}, which=lambda _: None)
        self.assertEqual(config.cli_candidates, ())


if __name__ == "__main__":
    unittest.main()
