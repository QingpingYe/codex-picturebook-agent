import unittest
import tempfile
from pathlib import Path

from config_paths import ConfigResolutionError, resolve_config_path


class ResolverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()

    def write(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
        return path

    def test_explicit_path_has_highest_priority(self):
        explicit = self.write(self.root / "explicit.json")
        environment = self.write(self.root / "environment.json")
        self.write(self.workspace / "feishu-knowledge-base.json")
        resolved = resolve_config_path(
            explicit, self.workspace,
            {"PICTUREBOOK_KB_CONFIG": str(environment)}, home=self.root,
        )
        self.assertEqual(resolved.path, explicit)
        self.assertEqual(resolved.origin, "explicit")

    def test_environment_precedes_workspace(self):
        environment = self.write(self.root / "environment.json")
        self.write(self.workspace / "feishu-knowledge-base.json")
        resolved = resolve_config_path(
            None, self.workspace,
            {"PICTUREBOOK_KB_CONFIG": str(environment)}, home=self.root,
        )
        self.assertEqual(resolved.path, environment)
        self.assertEqual(resolved.origin, "environment")

    def test_workspace_precedes_user_file(self):
        workspace = self.write(self.workspace / "feishu-knowledge-base.json")
        user = self.write(self.root / ".picturebook-screenwriter" / "feishu-knowledge-base.json")
        resolved = resolve_config_path(None, self.workspace, {}, home=self.root)
        self.assertEqual(resolved.path, workspace)
        self.assertEqual(resolved.origin, "workspace")
        self.assertIn(user, resolved.searched)

    def test_missing_direct_path_does_not_fall_back(self):
        self.write(self.workspace / "feishu-knowledge-base.json")
        with self.assertRaises(ConfigResolutionError) as caught:
            resolve_config_path(
                self.root / "missing.json", self.workspace, {}, home=self.root,
            )
        self.assertEqual(caught.exception.status, "missing_config")
        self.assertEqual(caught.exception.searched, (self.root / "missing.json",))

    def test_missing_discovery_reports_all_fallback_candidates(self):
        user = self.root / ".picturebook-screenwriter" / "feishu-knowledge-base.json"
        with self.assertRaises(ConfigResolutionError) as caught:
            resolve_config_path(None, self.workspace, {}, home=self.root)
        self.assertEqual(
            caught.exception.searched,
            (self.workspace / "feishu-knowledge-base.json", user),
        )


if __name__ == "__main__":
    unittest.main()
