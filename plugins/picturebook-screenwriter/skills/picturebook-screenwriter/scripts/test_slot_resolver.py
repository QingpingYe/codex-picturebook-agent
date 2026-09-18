import unittest
from slot_resolver import resolve_slot, SlotRequest


class SlotResolverTests(unittest.TestCase):
    def test_accepts_contract_artifact_types(self):
        for artifact_type in (
            "positioning",
            "topic_plan",
            "worldview",
            "characters",
            "outline",
            "script",
        ):
            with self.subTest(artifact_type=artifact_type):
                request = SlotRequest(artifact_type, "海外绘本", "小老鼠迈尔斯")
                self.assertEqual(resolve_slot(request, "quality"), "quality-baseline")

    def test_baseline_is_selected(self):
        request = SlotRequest("script", "海外绘本", "小老鼠迈尔斯")
        self.assertEqual(resolve_slot(request, "pre_create"), "pre_create-baseline")

    def test_invalid_slot_is_rejected(self):
        request = SlotRequest("script", "海外绘本", "小老鼠迈尔斯")
        with self.assertRaisesRegex(ValueError, "slot"):
            resolve_slot(request, "unknown")


if __name__ == "__main__":
    unittest.main()
