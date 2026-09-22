"""Run all offline checks for the picture book plugin."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "picturebook-screenwriter"
PYTHON_TEST_ROOTS = (PLUGIN_ROOT, REPO_ROOT / "scripts")
EXCLUDED_DIRS = frozenset({".git", ".tmp", "__pycache__", "node_modules"})


class DiscoveryError(RuntimeError):
    """Raised when test discovery cannot produce a trustworthy file list."""


def _raise_walk_error(error: OSError) -> None:
    raise DiscoveryError(str(error)) from error


def discover_python_tests(roots: Sequence[Path]) -> list[Path]:
    """Return every Python test below the roots, excluding runtime directories."""
    tests: set[Path] = set()
    for root in roots:
        if not root.is_dir():
            raise DiscoveryError(f"test root does not exist: {root}")
        for dirpath, dirnames, filenames in os.walk(root, onerror=_raise_walk_error):
            dirnames[:] = sorted(
                name for name in dirnames if name not in EXCLUDED_DIRS
            )
            for filename in filenames:
                if filename.startswith("test_") and filename.endswith(".py"):
                    tests.add(Path(dirpath) / filename)
    if not tests:
        raise DiscoveryError("no Python tests discovered")
    return sorted(tests)


def build_commands(repo_root: Path) -> list[list[str]]:
    """Build the complete offline verification command sequence."""
    plugin_root = repo_root / "plugins" / "picturebook-screenwriter"
    python_tests = discover_python_tests(
        (plugin_root, repo_root / "scripts")
    )

    commands = [[sys.executable, str(path)] for path in python_tests]
    commands.extend(
        [
            [sys.executable, str(repo_root / "scripts" / "governance_check.py")],
            [sys.executable, str(repo_root / "scripts" / "package_check.py")],
            [
                "node",
                "--test",
                str(plugin_root / "skills" / "image-generate" / "generate.test.js"),
            ],
            [
                "node",
                str(
                    plugin_root
                    / "skills"
                    / "staging-planner"
                    / "scripts"
                    / "run_regression.js"
                ),
            ],
        ]
    )
    return commands


def _format_command(command: Sequence[str]) -> str:
    return subprocess.list2cmdline(list(command))


def main(argv: Sequence[str] | None = None) -> int:
    """Run every offline gate and return a non-zero status on any failure."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments:
        print("usage: python scripts/run_plugin_tests.py", file=sys.stderr)
        return 2

    try:
        commands = build_commands(REPO_ROOT)
    except DiscoveryError as error:
        print(f"ERROR: test discovery failed: {error}", file=sys.stderr)
        return 2

    failures: list[tuple[list[str], str]] = []
    for command in commands:
        print(f"[run] {_format_command(command)}", flush=True)
        try:
            result = subprocess.run(command, cwd=REPO_ROOT, check=False)
        except OSError as error:
            failures.append((command, str(error)))
            print(f"[error] {error}", file=sys.stderr, flush=True)
            continue
        if result.returncode != 0:
            failures.append((command, f"exit code {result.returncode}"))

    if failures:
        print(f"\nFAILED: {len(failures)} command(s)", file=sys.stderr)
        for command, reason in failures:
            print(f"- {_format_command(command)}: {reason}", file=sys.stderr)
        return 1

    print(f"\nOK: {len(commands)} offline commands passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
