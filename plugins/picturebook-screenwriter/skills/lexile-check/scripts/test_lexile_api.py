import unittest

from lexile_api import FakeLexileClient


class LexileApiTests(unittest.TestCase):
    def test_fake_client_is_explicitly_measured(self):
        result = FakeLexileClient(320, "Grade 1").measure("hello")
        self.assertEqual(result.score, 320)
        self.assertTrue(result.measured)

    def test_negative_score_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "score"):
            FakeLexileClient(-1, "invalid").measure("hello")


if __name__ == "__main__":
    unittest.main()
