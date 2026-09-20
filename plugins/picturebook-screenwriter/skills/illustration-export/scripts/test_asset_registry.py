import unittest

from asset_registry import AssetRecord, parse_registry, render_registry


class AssetRegistryTests(unittest.TestCase):
    def test_round_trip(self):
        record = AssetRecord(
            "char-lulu",
            "character",
            "露露",
            "approved",
            "lulu_v1.png",
            {"worldview": 42},
        )

        parsed = parse_registry(render_registry([record]))

        self.assertEqual(parsed, (record,))


if __name__ == "__main__":
    unittest.main()
