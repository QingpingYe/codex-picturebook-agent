import unittest

from prompt_contract import PromptContractError, validate_prompt_payload


class PromptContractTests(unittest.TestCase):
    def test_valid_payload_passes(self):
        payload = {
            "pages": [
                {
                    "page": 1,
                    "prompt": ["主体", "空间"],
                    "prompt_text": "主体。空间",
                    "risks_found": [],
                    "assumptions": [],
                    "staging": {"frame_laws": ["河流从左到右"]},
                }
            ]
        }

        validate_prompt_payload(payload)

    def test_missing_frame_law_fails(self):
        payload = {
            "pages": [
                {
                    "page": 1,
                    "prompt": ["主体"],
                    "prompt_text": "主体",
                }
            ]
        }

        with self.assertRaisesRegex(PromptContractError, "frame_laws"):
            validate_prompt_payload(payload)


if __name__ == "__main__":
    unittest.main()
