import sys
import tempfile
import unittest
from pathlib import Path


try:
    import run_plugin_tests as runner
except ModuleNotFoundError as exc:
    runner = None
    IMPORT_ERROR = exc
else:
    IMPORT_ERROR = None


class PluginTestRunnerTests(unittest.TestCase):
    def setUp(self):
        if runner is None:
            self.fail(f"aggregate runner module is missing: {IMPORT_ERROR}")

    def test_discover_python_tests_prunes_runtime_directories(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            expected = [
                root / "scripts" / "test_root.py",
                root / "tests" / "test_top.py",
                root / "skills" / "nested" / "scripts" / "test_nested.py",
            ]
            for path in expected:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("", encoding="utf-8")

            for excluded in (
                root / ".tmp" / "test_tmp.py",
                root / "__pycache__" / "test_cache.py",
                root / "skills" / "nested" / "node_modules" / "test_node.py",
            ):
                excluded.parent.mkdir(parents=True, exist_ok=True)
                excluded.write_text("", encoding="utf-8")

            self.assertEqual(
                runner.discover_python_tests([root / "scripts", root / "tests", root / "skills"]),
                sorted(expected),
            )

    def test_build_commands_includes_every_gate_family(self):
        repo_root = Path(__file__).resolve().parents[1]
        commands = runner.build_commands(repo_root)
        flattened = [" ".join(command) for command in commands]

        self.assertTrue(any("test_plugin_contract.py" in command for command in flattened))
        self.assertTrue(any("governance_check.py" in command for command in flattened))
        self.assertTrue(any("package_check.py" in command for command in flattened))
        self.assertTrue(any("generate.test.js" in command for command in flattened))
        self.assertTrue(any("run_regression.js" in command for command in flattened))


if __name__ == "__main__":
    unittest.main()
