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
        self.assertNotIn(user, resolved.searched)

    def test_missing_direct_path_does_not_fall_back(self):
        self.write(self.workspace / "feishu-knowledge-base.json")
        with self.assertRaises(ConfigResolutionError) as caught:
            resolve_config_path(
                self.root / "missing.json", self.workspace, {}, home=self.root,
            )
        self.assertEqual(caught.exception.status, "missing_config")
        self.assertEqual(caught.exception.searched, (self.root / "missing.json",))

    def test_missing_discovery_reports_all_fallback_candidates(self):
        user = self.root / "home" / ".picturebook-screenwriter" / "feishu-knowledge-base.json"
        with self.assertRaises(ConfigResolutionError) as caught:
            resolve_config_path(None, self.workspace, {}, home=self.root / "home")
        self.assertIn(self.workspace / "feishu-knowledge-base.json", caught.exception.searched)
        self.assertIn(user, caught.exception.searched)

    def test_nearest_ancestor_config_wins(self):
        root_config = self.write(self.root / "feishu-knowledge-base.json")
        nested = self.root / "shared" / "project"
        nested.mkdir(parents=True)
        resolved = resolve_config_path(None, nested, {}, home=self.root / "home")
        self.assertEqual(resolved.path, root_config)
        self.assertEqual(resolved.origin, "ancestor")

    def test_nearest_ancestor_beats_higher_ancestor(self):
        shared = self.root / "shared"
        shared.mkdir()
        shared_config = self.write(shared / "feishu-knowledge-base.json")
        self.write(self.root / "feishu-knowledge-base.json")
        resolved = resolve_config_path(
            None, shared / "project", {}, home=self.root / "home",
        )
        self.assertEqual(resolved.path, shared_config)

    def test_deeper_child_config_is_not_discovered(self):
        nested = self.workspace / "project"
        self.write(nested / "feishu-knowledge-base.json")
        with self.assertRaises(ConfigResolutionError):
            resolve_config_path(None, self.workspace, {}, home=self.root / "home")

    def test_windows_user_directory_is_portable(self):
        appdata = self.root / "appdata"
        expected = appdata / "picturebook-screenwriter" / "feishu-knowledge-base.json"
        self.write(expected)
        resolved = resolve_config_path(
            None, self.workspace, {"APPDATA": str(appdata)},
            home=self.root / "home", system="windows",
        )
        self.assertEqual(resolved.path, expected)
        self.assertEqual(resolved.origin, "user")

    def test_linux_xdg_user_directory_is_portable(self):
        config_home = self.root / "xdg"
        expected = config_home / "picturebook-screenwriter" / "feishu-knowledge-base.json"
        self.write(expected)
        resolved = resolve_config_path(
            None, self.workspace, {"XDG_CONFIG_HOME": str(config_home)},
            home=self.root / "home", system="linux",
        )
        self.assertEqual(resolved.path, expected)
        self.assertEqual(resolved.origin, "user")

    def test_macos_user_directory_is_portable(self):
        expected = self.root / "home" / "Library" / "Application Support" / \
            "picturebook-screenwriter" / "feishu-knowledge-base.json"
        self.write(expected)
        resolved = resolve_config_path(
            None, self.workspace, {}, home=self.root / "home", system="macos",
        )
        self.assertEqual(resolved.path, expected)
        self.assertEqual(resolved.origin, "user")


if __name__ == "__main__":
    unittest.main()
