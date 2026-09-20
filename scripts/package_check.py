"""Offline package inventory and credential-path validation."""

import json
import re
from dataclasses import dataclass
from pathlib import Path


_JSON_SECRET_SUFFIXES = ("api_key", "apikey", "authorization", "password", "secret", "token")


@dataclass(frozen=True)
class PackageReport:
    ok: bool
    version: str
    file_count: int
    files: tuple[str, ...]
    errors: tuple[str, ...]


def _check_json_secret_values(path: Path, relative: str, errors: list[str]) -> None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return

    def visit(value, path_name: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                child_path = f"{path_name}.{key}"
                key_name = str(key).lower()
                if (
                    key_name.endswith(_JSON_SECRET_SUFFIXES)
                    and isinstance(child, str)
                    and child
                    and not child.startswith(("REPLACE_WITH_", "<"))
                ):
                    errors.append(f"non-placeholder credential value: {child_path}")
                visit(child, child_path)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f"{path_name}[{index}]")

    visit(payload, relative)


def build_package_report(root: Path) -> PackageReport:
    errors: list[str] = []
    plugin = root / "plugins" / "picturebook-screenwriter"
    manifest_path = plugin / ".codex-plugin" / "plugin.json"

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        version = str(manifest.get("version", ""))
    except (OSError, ValueError) as exc:
        return PackageReport(False, "", 0, (), (f"invalid plugin.json: {exc}",))

    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        errors.append("manifest version must be semantic versioning")

    for relative in (
        "README.md",
        "config/plugin-contract.json",
        ".codex-plugin/plugin.json",
        "skills/picturebook-screenwriter/SKILL.md",
    ):
        if not (plugin / relative).is_file():
            errors.append(f"missing package file: {relative}")

    files: list[str] = []
    excluded_parts = {"__pycache__", "node_modules", ".picturebook-screenwriter"}
    for path in plugin.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(plugin)
        if any(part in excluded_parts for part in relative.parts):
            continue
        if path.name == ".env" or path.suffix in {".key", ".pem"}:
            errors.append(f"forbidden package file: {relative.as_posix()}")
            continue
        if path.suffix == ".json":
            _check_json_secret_values(path, relative.as_posix(), errors)
        files.append(relative.as_posix())

    return PackageReport(
        ok=not errors,
        version=version,
        file_count=len(files),
        files=tuple(sorted(files)),
        errors=tuple(errors),
    )


def main() -> int:
    report = build_package_report(Path(__file__).resolve().parents[1])
    for error in report.errors:
        print(f"ERROR: {error}")
    print(f"package files: {report.file_count}; version: {report.version}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
