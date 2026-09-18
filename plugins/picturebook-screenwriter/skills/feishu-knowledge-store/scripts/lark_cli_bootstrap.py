#!/usr/bin/env python3
"""Check for a compatible Lark CLI and optionally run its official installer."""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from lark_cli import LarkCli


SUPPORTED_VERSIONS = {"1.0.95", "1.0.96"}
WINDOWS_CLI_FALLBACK = Path(r"D:\lark-cli\lark-cli.exe")
INSTALL_COMMAND = ("npx", "@larksuite/cli@latest", "install")
_VERSION = re.compile(r"lark-cli.*?([0-9]+\.[0-9]+\.[0-9]+)", re.IGNORECASE)


class BootstrapError(RuntimeError):
    pass


def ensure_lark_cli(
    candidates: Iterable[Path | str] | None = None,
    install: bool = False,
    runner: Callable[..., Any] = subprocess.run,
    environ: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
    installer_command: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Return CLI status, installing only when explicitly requested."""
    environment = os.environ if environ is None else environ
    selected = tuple(candidates) if candidates is not None else default_cli_candidates(environment, which)
    installed = False
    found = _inspect_candidates(selected, runner)

    if found["status"] == "available":
        return {**found, "installed": installed}
    if found["status"] == "unsupported":
        return {**found, "installed": installed}
    if not install:
        return {
            "status": "missing",
            "installed": installed,
            "install_command": list(installer_command or INSTALL_COMMAND),
        }

    command = list(installer_command or INSTALL_COMMAND)
    try:
        completed = runner(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except FileNotFoundError as error:
        raise BootstrapError(
            "lark-cli installer unavailable: npx not found. Install Node.js 16 or newer, then retry."
        ) from error
    except OSError as error:
        raise BootstrapError("could not execute lark-cli installer: " + LarkCli._redact(str(error))) from error

    if getattr(completed, "returncode", 1) != 0:
        output = " ".join(
            part for part in (
                str(getattr(completed, "stderr", "") or ""),
                str(getattr(completed, "stdout", "") or ""),
            ) if part
        )
        raise BootstrapError("lark-cli installer failed: " + LarkCli._redact(output))

    installed = True
    recheck_candidates: list[Path] = []
    for candidate in (*default_cli_candidates(environment, which), *selected):
        if candidate not in recheck_candidates:
            recheck_candidates.append(candidate)
    rechecked = _inspect_candidates(recheck_candidates, runner)
    if rechecked["status"] == "available":
        return {**rechecked, "installed": installed}
    if rechecked["status"] == "unsupported":
        return {**rechecked, "installed": installed}
    return {
        "status": "missing",
        "installed": installed,
        "reason": "installer completed, but no supported lark-cli was found; reopen the terminal and run the check again",
        "install_command": command,
    }


def default_cli_candidates(
    environment: Mapping[str, str], which: Callable[[str], str | None]
) -> tuple[Path, ...]:
    candidates: list[Path] = []
    for candidate in (
        environment.get("LARK_CLI_PATH"),
        which("lark-cli"),
    ):
        if candidate:
            path = Path(candidate)
            if path not in candidates:
                candidates.append(path)
    candidates.extend(_platform_candidates(environment))
    return tuple(candidates)


def _platform_candidates(environment: Mapping[str, str]) -> tuple[Path, ...]:
    if os.name == "nt":
        candidates: list[Path] = []
        appdata = environment.get("APPDATA")
        if appdata:
            npm_bin = Path(appdata) / "npm"
            candidates.extend((
                npm_bin / "lark-cli.cmd",
                npm_bin / "node_modules" / "@larksuite" / "cli" / "bin" / "lark-cli.exe",
            ))
        program_files = environment.get("ProgramFiles")
        if program_files:
            candidates.append(Path(program_files) / "nodejs" / "lark-cli.cmd")
        candidates.extend((
            Path(r"C:\lark-cli\lark-cli.exe"),
            WINDOWS_CLI_FALLBACK,
        ))
        return tuple(candidates)

    home = environment.get("HOME")
    candidates = []
    if home:
        candidates.append(Path(home) / ".local" / "bin" / "lark-cli")
    candidates.extend((
        Path("/usr/local/bin/lark-cli"),
        Path("/opt/homebrew/bin/lark-cli"),
    ))
    return tuple(candidates)


def _inspect_candidates(candidates: Iterable[Path | str], runner: Callable[..., Any]) -> dict[str, Any]:
    unsupported: dict[str, Any] | None = None
    for candidate in candidates:
        path = Path(candidate)
        try:
            completed = runner(
                [str(path), "--version"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except FileNotFoundError:
            continue
        except OSError as error:
            raise BootstrapError("could not execute lark-cli: " + LarkCli._redact(str(error))) from error

        output = " ".join(
            part for part in (
                str(getattr(completed, "stdout", "") or ""),
                str(getattr(completed, "stderr", "") or ""),
            ) if part
        )
        match = _VERSION.search(output)
        if match is None:
            if getattr(completed, "returncode", 0) != 0:
                continue
            unsupported = {
                "status": "unsupported",
                "path": str(path),
                "version": None,
                "reason": "lark-cli version could not be recognized",
            }
            continue
        version = match.group(1)
        if version in SUPPORTED_VERSIONS:
            return {"status": "available", "path": str(path), "version": version}
        unsupported = {"status": "unsupported", "path": str(path), "version": version}
    if unsupported is not None:
        return unsupported
    return {"status": "missing"}


def _resolved_installer_command() -> list[str]:
    command = list(INSTALL_COMMAND)
    executable = shutil.which(command[0])
    if executable:
        command[0] = executable
    return command


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check and optionally install the Feishu Lark CLI")
    parser.add_argument(
        "--install",
        action="store_true",
        help="run the official installer when no compatible CLI is found",
    )
    arguments = parser.parse_args(argv)
    try:
        result = ensure_lark_cli(install=arguments.install, installer_command=_resolved_installer_command())
    except BootstrapError as error:
        print(json.dumps({"status": "error", "message": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "available" else 1


if __name__ == "__main__":
    raise SystemExit(main())
