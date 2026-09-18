import unittest

from collision import check_collisions


class CollisionTests(unittest.TestCase):
    def test_shared_character_name_is_reported(self):
        bundle = {"items": [{"key": "series/common/characters", "content": "迈尔斯是一只小老鼠。"}]}
        hits = check_collisions("迈尔斯开始冒险。", bundle, ("迈尔斯",))
        self.assertEqual(len(hits), 1)
        self.assertIn("迈尔斯", hits[0].evidence_excerpt)


if __name__ == "__main__":
    unittest.main()
