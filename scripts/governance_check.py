"""Offline governance checks for the picture book plugin repository."""

import json
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GovernanceReport:
    ok: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, ValueError) as exc:
        return None, str(exc)


def check_repo(root: Path) -> GovernanceReport:
    errors: list[str] = []
    warnings: list[str] = []
    plugin = root / "plugins" / "picturebook-screenwriter"
    manifest_path = plugin / ".codex-plugin" / "plugin.json"
    contract_path = plugin / "config" / "plugin-contract.json"

    manifest, manifest_error = _read_json(manifest_path)
    if manifest_error:
        errors.append(f"invalid or missing plugin.json: {manifest_error}")
        manifest = None

    contract, contract_error = _read_json(contract_path)
    if contract_error:
        errors.append(f"invalid or missing plugin-contract.json: {contract_error}")
        contract = None

    if manifest and not re.fullmatch(r"\d+\.\d+\.\d+", str(manifest.get("version", ""))):
        errors.append("manifest version must be semantic versioning")

    if contract:
        if contract.get("schema_version") != 1:
            errors.append("plugin-contract.json schema_version must be 1")
        entry_skill = contract.get("entry_skill")
        installed = {
            path.name
            for path in (plugin / "skills").iterdir()
            if path.is_dir() and (path / "SKILL.md").exists()
        }
        declared = set(contract.get("skills", []))
        if declared != installed:
            errors.append(
                "skill set mismatch: contract-only="
                + ",".join(sorted(declared - installed))
                + "; installed-only="
                + ",".join(sorted(installed - declared))
            )
        if entry_skill not in installed:
            errors.append(f"entry skill is not installed: {entry_skill}")

    for relative in (".env", ".picturebook-screenwriter"):
        if (plugin / relative).exists():
            errors.append(f"forbidden runtime path in plugin: {relative}")

    return GovernanceReport(not errors, tuple(errors), tuple(warnings))


def main() -> int:
    report = check_repo(Path(__file__).resolve().parents[1])
    for error in report.errors:
        print(f"ERROR: {error}")
    for warning in report.warnings:
        print(f"WARN: {warning}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
