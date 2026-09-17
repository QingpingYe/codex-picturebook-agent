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
        "schema_version": 1,
        "source_wiki_url": "https://wcno1rbz0o8i.feishu.cn/wiki/OI9gwaRv8i3RwOkGBNnc7VHinDd",
        "target_root_token": "T08vwqXroiuJEfkoVzFcRaFXnMf",
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

    def test_environment_path_wins(self):
        path = self.tmp / "from-environment.json"
        config = self.load(config_json({}), {"PICTUREBOOK_KB_CONFIG": str(path)})
        self.assertEqual(config.target_root_token, "T08vwqXroiuJEfkoVzFcRaFXnMf")

    def test_bot_identity_is_rejected(self):
        with self.assertRaisesRegex(ConfigError, "identity must be 'user'"):
            self.load(config_json({"identity": "bot"}), {})

    def test_boolean_schema_version_is_rejected(self):
        with self.assertRaisesRegex(ConfigError, "schema_version must be 1"):
            self.load(config_json({"schema_version": True}), {})

    def test_rejects_unknown_and_missing_fields(self):
        with self.assertRaisesRegex(ConfigError, "unexpected"):
            self.load(config_json({"extra": True}), {})
        with self.assertRaisesRegex(ConfigError, "non-empty"):
            self.load(config_json({"source_wiki_url": None}), {})

    def test_cli_candidates_follow_portable_order_without_duplicates(self):
        config = self.load(config_json({}), {"LARK_CLI_PATH": "lark-cli"}, which=lambda _: "lark-cli")
        self.assertEqual(config.cli_candidates[0], Path("lark-cli"))
        self.assertEqual(len(config.cli_candidates), 2)


if __name__ == "__main__":
    unittest.main()
