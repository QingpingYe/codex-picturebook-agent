import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"


class CiWorkflowTests(unittest.TestCase):
    def test_ci_runs_the_aggregate_runner_on_supported_platforms(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        for value in (
            "push:",
            "pull_request:",
            "ubuntu-latest",
            "windows-latest",
            "actions/setup-python@v5",
            'python-version: "3.12"',
            "actions/setup-node@v4",
            'node-version: "22"',
            "python scripts/run_plugin_tests.py",
        ):
            with self.subTest(value=value):
                self.assertIn(value, text)


if __name__ == "__main__":
    unittest.main()
