import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from lark_cli_bootstrap import BootstrapError, default_cli_candidates, ensure_lark_cli


class Completed:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


class Runner:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.commands = []

    def __call__(self, command, **_kwargs):
        self.commands.append(command)
        if self.responses:
            return self.responses.pop(0)
        return Completed("", returncode=1, stderr="unexpected command")


class LarkCliBootstrapTests(unittest.TestCase):
    def test_default_candidates_include_portable_windows_locations(self):
        environ = {
            "LARK_CLI_PATH": r"C:\Explicit\lark-cli.exe",
            "APPDATA": r"C:\Users\writer\AppData\Roaming",
            "ProgramFiles": r"C:\Program Files",
        }
        candidates = default_cli_candidates(
            environ,
            which=lambda name: r"C:\Windows\lark-cli.exe" if name == "lark-cli" else None,
        )
        self.assertEqual(list(candidates), [
            Path(r"C:\Explicit\lark-cli.exe"),
            Path(r"C:\Windows\lark-cli.exe"),
            Path(r"C:\Users\writer\AppData\Roaming\npm\lark-cli.cmd"),
            Path(r"C:\Users\writer\AppData\Roaming\npm\node_modules\@larksuite\cli\bin\lark-cli.exe"),
            Path(r"C:\Program Files\nodejs\lark-cli.cmd"),
        ])

    def test_bootstrap_platform_candidates_exclude_author_drive_letters(self):
        candidates = default_cli_candidates({
            "APPDATA": r"C:\Users\writer\AppData\Roaming",
            "ProgramFiles": r"C:\Program Files",
            "HOME": r"C:\Users\writer",
        }, which=lambda _: None)
        text = "\n".join(str(path) for path in candidates)
        self.assertNotIn("C:\\lark-cli", text)
        self.assertNotIn("D:\\lark-cli", text)

    def test_existing_supported_cli_is_used_without_install(self):
        runner = Runner(Completed("lark-cli version 1.0.95\n"))
        result = ensure_lark_cli(
            candidates=(Path(r"C:\Tools\lark-cli.exe"),),
            runner=runner,
        )
        self.assertEqual(result, {
            "status": "available",
            "path": r"C:\Tools\lark-cli.exe",
            "version": "1.0.95",
            "installed": False,
        })
        self.assertEqual(runner.commands, [[r"C:\Tools\lark-cli.exe", "--version"]])

    def test_missing_cli_reports_official_install_command(self):
        runner = Runner(Completed("not found", returncode=1))
        result = ensure_lark_cli(
            candidates=(Path(r"C:\Tools\lark-cli.exe"),),
            runner=runner,
        )
        self.assertEqual(result["status"], "missing")
        self.assertEqual(result["install_command"], ["npx", "@larksuite/cli@latest", "install"])
        self.assertFalse(runner.commands[1:])

    def test_install_flag_runs_installer_and_rechecks(self):
        runner = Runner(
            Completed("not found", returncode=1),
            Completed(""),
            Completed("lark-cli version 1.0.96\n"),
        )
        result = ensure_lark_cli(
            candidates=(Path(r"C:\Tools\lark-cli.exe"),),
            install=True,
            runner=runner,
        )
        self.assertEqual(result["status"], "available")
        self.assertTrue(result["installed"])
        self.assertEqual(runner.commands[1], ["npx", "@larksuite/cli@latest", "install"])

    def test_install_refreshes_cli_candidates_from_path(self):
        runner = Runner(
            Completed("not found", returncode=1),
            Completed(""),
            Completed("lark-cli version 1.0.95\n"),
        )
        installed_path = Path(r"C:\npm-global\lark-cli.cmd")

        def which(name):
            return str(installed_path) if name == "lark-cli" else None

        result = ensure_lark_cli(
            candidates=(Path(r"C:\Tools\lark-cli.exe"),),
            install=True,
            runner=runner,
            environ={},
            which=which,
        )
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["path"], str(installed_path))
        self.assertEqual(runner.commands[-1], [str(installed_path), "--version"])

    def test_unsupported_version_is_not_replaced(self):
        runner = Runner(Completed("lark-cli version 1.0.94\n"))
        result = ensure_lark_cli(
            candidates=(Path(r"C:\Tools\lark-cli.exe"),),
            install=True,
            runner=runner,
        )
        self.assertEqual(result["status"], "unsupported")
        self.assertEqual(result["version"], "1.0.94")
        self.assertEqual(runner.commands, [[r"C:\Tools\lark-cli.exe", "--version"]])

    def test_installer_failure_is_reported_with_redacted_output(self):
        runner = Runner(
            Completed("not found", returncode=1),
            Completed("npm error token=secret-value-123456789012345", returncode=1),
        )
        with self.assertRaises(BootstrapError) as raised:
            ensure_lark_cli(
                candidates=(Path(r"C:\Tools\lark-cli.exe"),),
                install=True,
                runner=runner,
            )
        self.assertIn("lark-cli installer failed", str(raised.exception))
        self.assertNotIn("secret-value", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
