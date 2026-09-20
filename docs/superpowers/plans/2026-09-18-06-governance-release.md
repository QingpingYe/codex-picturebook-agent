# Governance and Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Revision:** 2026-09-20, after the Phase 5 art/export branch was merged into `main`.

**Goal:** Make the Phase 1-5 plugin auditable, self-checking, versioned, packageable, and ready for explicit multi-user release acceptance.

**Architecture:** Align the public release surface with the completed art/export phase, add a strict local session-audit skill, and add repo-level governance and package inventory checks. Release documents become executable human gates; live Feishu, image API, and marketplace acceptance remain separate explicit user actions.

**Tech Stack:** Python 3.10+ standard library, `unittest`, JSON, Markdown, Node.js 18+ for existing regressions, Git, and Codex plugin packaging.

**Spec:** `docs/superpowers/specs/2026-09-18-workbuddy-codex-plugin-roadmap.md`

## Global Constraints

- Session export must not write into the plugin installation directory.
- Session export output directories must be absolute and explicitly supplied.
- Governance, package, and release checks must be deterministic and offline.
- The next release version is `0.3.0`; it must follow semantic versioning.
- User-visible plugin text is Chinese.
- Files are written only after explicit approval or an explicit export request.
- No credentials may appear in source, logs, exports, package inventories, or git history.
- Offline verification must not call Feishu, image generation APIs, or any network service.
- Live Feishu, live image generation, and marketplace publication require explicit user participation and are not part of CI.
- No WorkBuddy-only APIs, agents, hooks, or filesystem assumptions may leak into plugin runtime code.

## Review Focus

- Relative session export output directories must fail closed rather than resolving against an assumed working directory; Task 2 tests this.
- A session ID containing `/`, `\`, `..`, or another path separator must never create a nested or escaped bundle; Task 2 tests this.
- An output path inside the plugin installation directory must be rejected even when the caller says it was explicit; Task 2 tests this.
- A contract skill missing from `skills/`, or an installed skill missing from the contract, must make governance fail; Task 4 tests both directions.
- A forbidden credential file such as `.env`, `*.key`, or `*.pem` must prevent package validation from reporting success; Task 5 tests this.

---

### Task 1: Align the public release surface with Phase 5

**Files:**
- Modify: `plugins/picturebook-screenwriter/.codex-plugin/plugin.json`
- Modify: `plugins/picturebook-screenwriter/config/plugin-contract.json`
- Modify: `plugins/picturebook-screenwriter/README.md`
- Modify: `README.md`
- Test: `plugins/picturebook-screenwriter/tests/test_release_contract.py`

**Interfaces:**
- Produces: manifest version target `0.3.0`, Phase 5 artifact types, and public documentation that no longer claims art/export is unsupported.

- [x] **Step 1: Add the failing release-surface tests**

Create `plugins/picturebook-screenwriter/tests/test_release_contract.py`:

```python
import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
MANIFEST = ROOT / ".codex-plugin" / "plugin.json"
CONTRACT = ROOT / "config" / "plugin-contract.json"


class ReleaseSurfaceTests(unittest.TestCase):
    def test_manifest_describes_completed_art_phase(self):
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.assertIn("gated illustration workflow", manifest["description"])
        self.assertIn(
            "为已批准的绘本脚本生成结构化插画提示词",
            manifest["interface"]["defaultPrompt"],
        )

    def test_contract_declares_phase5_artifacts(self):
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        for artifact in (
            "illustration_prompt_payload",
            "image_asset",
            "asset_registry",
            "html_preview",
        ):
            with self.subTest(artifact=artifact):
                self.assertIn(artifact, contract["artifact_types"])

    def test_plugin_readme_no_longer_marks_art_as_unsupported(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("image-generate", text)
        self.assertIn("illustration-export", text)
        self.assertNotIn("- 插画与素材图生成\n", text)
        self.assertNotIn("- HTML 预览导出\n", text)

    def test_repo_readme_describes_self_contained_preview(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("gated illustration workflow", text)
        self.assertIn("self-contained HTML preview", text)


if __name__ == "__main__":
    unittest.main()
```

- [x] **Step 2: Run the test and confirm it fails**

Run: `python .\plugins\picturebook-screenwriter\tests\test_release_contract.py -v`

Expected: FAIL/ERROR because the manifest, contract, and READMEs do not yet declare the Phase 5 release surface.

- [x] **Step 3: Update the public metadata and contract**

In `plugin.json`, set the top-level `version` and `description`, and set `defaultPrompt` inside the existing `interface` object:

```json
{
  "version": "0.3.0",
  "description": "Picture book screenwriting workshop for Codex: editorial workflow, quality gates, staging planning, and a gated illustration workflow.",
  "interface": {
    "defaultPrompt": [
      "帮我从零开始策划一个绘本故事",
      "检查这份绘本脚本的工艺指标",
      "为逐页脚本规划分镜站位",
      "为已批准的绘本脚本生成结构化插画提示词"
    ]
  }
}
```

The consolidated view is:

```json
{
  "version": "0.3.0",
  "description": "Picture book screenwriting workshop for Codex: editorial workflow, quality gates, staging planning, and a gated illustration workflow.",
  "defaultPrompt": [
    "帮我从零开始策划一个绘本故事",
    "检查这份绘本脚本的工艺指标",
    "为逐页脚本规划分镜站位",
    "为已批准的绘本脚本生成结构化插画提示词"
  ]
}
```

Preserve all other existing manifest fields.

In `config/plugin-contract.json`, extend `artifact_types` with:

```json
[
  "illustration_prompt_payload",
  "image_asset",
  "asset_registry",
  "html_preview"
]
```

Preserve the existing six narrative artifact types and all existing contract sections.

- [x] **Step 4: Update both READMEs**

In `plugins/picturebook-screenwriter/README.md`, move these from “暂不支持” to “已支持”:

```markdown
- `image-prompt-architect`：结构化插画提示词；只输出 JSON，不生成图片
- `image-generate`：仅在用户明确确认后调用图片 API
- `illustration-export`：资产登记、本地 HTML 预览与显式图片内嵌
```

Remove the lines claiming these are unsupported. Keep WorkBuddy multi-agent runtime, hooks, and live API calls under explicit boundaries.

In root `README.md`, update the capability list to include:

```markdown
- Gated illustration workflow
- Self-contained HTML preview
```

- [x] **Step 5: Run the test and confirm it passes**

Run: `python .\plugins\picturebook-screenwriter\tests\test_release_contract.py -v`

Expected: 4 tests pass.

- [x] **Step 6: Commit**

```powershell
git add README.md .\plugins\picturebook-screenwriter\README.md .\plugins\picturebook-screenwriter\.codex-plugin\plugin.json .\plugins\picturebook-screenwriter\config\plugin-contract.json .\plugins\picturebook-screenwriter\tests\test_release_contract.py
git commit -m "docs: align release surface with art export"
```

---

### Task 2: Add the explicit session export skill

**Files:**
- Create: `plugins/picturebook-screenwriter/skills/session-export/SKILL.md`
- Create: `plugins/picturebook-screenwriter/skills/session-export/scripts/export_session.py`
- Test: `plugins/picturebook-screenwriter/skills/session-export/scripts/test_export_session.py`
- Modify: `plugins/picturebook-screenwriter/config/plugin-contract.json`
- Modify: `plugins/picturebook-screenwriter/tests/test_plugin_contract.py`
- Modify: `plugins/picturebook-screenwriter/skills/picturebook-screenwriter/SKILL.md`
- Modify: `plugins/picturebook-screenwriter/tests/test_skill_contract.py`

**Interfaces:**
- Produces:
  - `build_manifest(session_id: str, messages: list, files: list, images: list) -> dict`
  - `export_session(session_id: str, messages: list, files: list, images: list, output_dir: Path, plugin_root: Path) -> Path`
- Consumes: caller-supplied JSON-safe messages, file paths, and image paths; it must not invent missing data.

- [x] **Step 1: Add the contract and failing skill tests**

In `tests/test_plugin_contract.py`, add:

```python
def test_contract_declares_session_export(self):
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    self.assertIn("session-export", contract["skills"])
```

Create `skills/session-export/scripts/test_export_session.py`:

```python
import json
import tempfile
import unittest
from pathlib import Path

from export_session import build_manifest, export_session


class ExportSessionTests(unittest.TestCase):
    def test_manifest_counts_inputs_without_adding_content(self):
        manifest = build_manifest(
            "s1",
            [{"role": "user"}],
            ["picturebook/script_v1.md"],
            ["p1.png"],
        )
        self.assertEqual(
            manifest["summary"],
            {"messages": 1, "files": 1, "images": 1},
        )

    def test_output_dir_must_be_absolute(self):
        with self.assertRaisesRegex(ValueError, "absolute"):
            export_session("s1", [], [], [], Path("exports"), Path.cwd())

    def test_rejects_output_inside_plugin(self):
        with tempfile.TemporaryDirectory() as temp:
            plugin_root = Path(temp) / "plugin"
            plugin_root.mkdir()
            with self.assertRaisesRegex(ValueError, "outside the plugin"):
                export_session("s1", [], [], [], plugin_root, plugin_root)

    def test_rejects_unsafe_session_id(self):
        with tempfile.TemporaryDirectory() as temp:
            output_dir = Path(temp)
            with self.assertRaisesRegex(ValueError, "session id"):
                export_session(
                    "../outside",
                    [],
                    [],
                    [],
                    output_dir,
                    Path(temp) / "plugin",
                )

    def test_rejects_secret_fields(self):
        with tempfile.TemporaryDirectory() as temp:
            messages = [{"metadata": {"api_key": "not-for-export"}}]
            with self.assertRaisesRegex(ValueError, "secret field"):
                export_session(
                    "s1",
                    messages,
                    [],
                    [],
                    Path(temp),
                    Path(temp) / "plugin",
                )

    def test_writes_explicit_bundle(self):
        with tempfile.TemporaryDirectory() as temp:
            output_dir = Path(temp)
            plugin_root = output_dir / "plugin"
            plugin_root.mkdir()
            bundle = export_session(
                "s1",
                [{"role": "user"}],
                ["picturebook/script_v1.md"],
                ["p1.png"],
                output_dir,
                plugin_root,
            )
            manifest = json.loads(
                (bundle / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["summary"]["messages"], 1)
            self.assertEqual(
                json.loads((bundle / "files.json").read_text(encoding="utf-8")),
                ["picturebook/script_v1.md"],
            )


if __name__ == "__main__":
    unittest.main()
```

- [x] **Step 2: Run the test and confirm it fails**

Run: `python .\plugins\picturebook-screenwriter\skills\session-export\scripts\test_export_session.py -v`

Expected: ERROR because `export_session.py` does not exist.

- [x] **Step 3: Implement the strict export boundary**

Create `skills/session-export/scripts/export_session.py`:

```python
"""Codex-local session audit bundle builder."""

import json
import re
from datetime import datetime, timezone
from pathlib import Path


_SECRET_KEYS = {"api_key", "apikey", "authorization", "password", "secret", "token"}


def build_manifest(session_id: str, messages: list, files: list, images: list) -> dict:
    return {
        "source": {"session_id": session_id},
        "summary": {
            "messages": len(messages),
            "files": len(files),
            "images": len(images),
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _assert_no_secret_keys(value, path: str = "input") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if str(key).lower() in _SECRET_KEYS:
                raise ValueError(f"secret field not allowed: {child_path}")
            _assert_no_secret_keys(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_no_secret_keys(child, f"{path}[{index}]")


def export_session(
    session_id: str,
    messages: list,
    files: list,
    images: list,
    output_dir: Path,
    plugin_root: Path,
) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", session_id):
        raise ValueError("session id must contain only ASCII letters, digits, _ or -")

    output_dir = Path(output_dir)
    if not output_dir.is_absolute():
        raise ValueError("output directory must be an absolute path")

    _assert_no_secret_keys(messages, "messages")
    _assert_no_secret_keys(files, "files")
    _assert_no_secret_keys(images, "images")

    plugin_root = Path(plugin_root).resolve()
    bundle = output_dir / f"{session_id}-audit"
    resolved_bundle = bundle.resolve()
    if resolved_bundle == plugin_root or plugin_root in resolved_bundle.parents:
        raise ValueError("output directory must be outside the plugin")

    bundle.mkdir(parents=True, exist_ok=True)
    payloads = {
        "manifest.json": build_manifest(session_id, messages, files, images),
        "messages.json": messages,
        "files.json": files,
        "images.json": images,
    }
    for name, payload in payloads.items():
        (bundle / name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return bundle
```

- [x] **Step 4: Add the skill and route**

Create `skills/session-export/SKILL.md` with this content:

```markdown
---
name: session-export
description: 导出当前会话的审计摘要与已收集内容；输出目录必须由用户显式指定。
---

# Session Export

## Boundary

- 只在用户明确要求导出会话证据时使用。
- 输出目录必须是用户提供的绝对路径。
- 不写入插件安装目录。
- 不读取、推断或记录 API key、令牌、密码或授权头。
- 只写入调用方已经提供的 JSON 安全数据。

## Workflow

1. 请用户提供绝对输出目录。
2. 收集本次会话中已经存在的对话事件、产物路径和图片路径。
3. 调用 `scripts/export_session.py`。
4. 用中文返回导出目录和消息、文件、图片数量。
```

Add `session-export` to `config/plugin-contract.json` skills.

In `skills/picturebook-screenwriter/SKILL.md`, add:

```markdown
## Session Export Gate

只有用户明确要求导出会话证据时，才调用 `../session-export/SKILL.md`。必须先要求用户给出绝对输出目录；不得默认选择路径，也不得写入插件目录。
```

In `tests/test_skill_contract.py`, add:

```python
def test_entry_routes_explicit_session_export(self):
    text = ENTRY_SKILL.read_text(encoding="utf-8")
    self.assertIn("session-export", text)
    self.assertIn("绝对输出目录", text)
    self.assertIn("不得默认选择路径", text)
```

- [x] **Step 5: Run focused and contract tests**

```powershell
python .\plugins\picturebook-screenwriter\skills\session-export\scripts\test_export_session.py -v
python .\plugins\picturebook-screenwriter\tests\test_plugin_contract.py -v
python .\plugins\picturebook-screenwriter\tests\test_skill_contract.py -v
```

Expected: all tests pass.

- [x] **Step 6: Commit**

```powershell
git add .\plugins\picturebook-screenwriter\skills\session-export .\plugins\picturebook-screenwriter\skills\picturebook-screenwriter\SKILL.md .\plugins\picturebook-screenwriter\config\plugin-contract.json .\plugins\picturebook-screenwriter\tests\test_plugin_contract.py .\plugins\picturebook-screenwriter\tests\test_skill_contract.py
git commit -m "feat: add explicit session export boundary"
```

---

### Task 3: Add version and changelog gates

**Files:**
- Create: `docs/CHANGELOG.md`
- Test: `plugins/picturebook-screenwriter/tests/test_release_contract.py`

**Interfaces:**
- Consumes: manifest version `0.3.0` from Task 1.
- Produces: a Keep a Changelog-style file that records the completed Phase 5 work under `[Unreleased]`.

- [x] **Step 1: Add failing release version tests**

Append to `tests/test_release_contract.py`:

```python
class ReleaseVersionTests(unittest.TestCase):
    def test_manifest_version_is_semver(self):
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.assertIsNotNone(re.fullmatch(r"\d+\.\d+\.\d+", manifest["version"]))

    def test_release_target_is_0_3_0(self):
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], "0.3.0")

    def test_changelog_records_phase5(self):
        text = (ROOT.parent.parent / "docs" / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn("## [Unreleased]", text)
        self.assertIn("Gated illustration workflow", text)
        self.assertIn("Self-contained HTML preview", text)
```

- [x] **Step 2: Run the test and confirm it fails**

Run: `python .\plugins\picturebook-screenwriter\tests\test_release_contract.py -v`

Expected: FAIL because `docs/CHANGELOG.md` does not exist.

- [x] **Step 3: Create the changelog**

Create `docs/CHANGELOG.md`:

```markdown
# Changelog

## [Unreleased]

### Added

- Codex-native picture-book editorial workflow and baseline slots.
- Feishu authoritative knowledge synchronization and retrieval.
- Craft benchmark, project red lines, Wiki lint, and optional Lexile gates.
- Gated illustration workflow with structured prompt payloads.
- Self-contained HTML preview and illustration asset registry.

### Changed

- Split WorkBuddy reference material out of the Codex plugin runtime.
```

If Task 1 did not already set the manifest version to `0.3.0`, update it now.

- [x] **Step 4: Run the test and confirm it passes**

Run: `python .\plugins\picturebook-screenwriter\tests\test_release_contract.py -v`

Expected: 7 tests pass.

- [x] **Step 5: Commit**

```powershell
git add docs\CHANGELOG.md .\plugins\picturebook-screenwriter\.codex-plugin\plugin.json .\plugins\picturebook-screenwriter\tests\test_release_contract.py
git commit -m "chore: add release versioning"
```

---

### Task 4: Add deterministic governance checks

**Files:**
- Create: `scripts/governance_check.py`
- Test: `scripts/test_governance_check.py`

**Interfaces:**
- Produces:
  - `GovernanceReport(ok: bool, errors: tuple[str, ...], warnings: tuple[str, ...])`
  - `check_repo(root: Path) -> GovernanceReport`
  - CLI: `python scripts/governance_check.py`

- [x] **Step 1: Write failing governance tests**

Create `scripts/test_governance_check.py`:

```python
import json
import tempfile
import unittest
from pathlib import Path

from governance_check import check_repo


def write_plugin(root: Path, contract_skills: list[str]) -> Path:
    plugin = root / "plugins" / "picturebook-screenwriter"
    (plugin / ".codex-plugin").mkdir(parents=True)
    (plugin / "config").mkdir()
    (plugin / "skills" / "picturebook-screenwriter").mkdir(parents=True)
    (plugin / "skills" / "picturebook-screenwriter" / "SKILL.md").write_text(
        "# entry",
        encoding="utf-8",
    )
    (plugin / ".codex-plugin" / "plugin.json").write_text(
        json.dumps({"version": "0.3.0"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (plugin / "config" / "plugin-contract.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "entry_skill": "picturebook-screenwriter",
                "skills": contract_skills,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return plugin


class GovernanceCheckTests(unittest.TestCase):
    def test_missing_plugin_manifest_is_error(self):
        with tempfile.TemporaryDirectory() as temp:
            report = check_repo(Path(temp))
            self.assertFalse(report.ok)
            self.assertTrue(any("plugin.json" in error for error in report.errors))

    def test_missing_contract_is_error(self):
        with tempfile.TemporaryDirectory() as temp:
            plugin = Path(temp) / "plugins" / "picturebook-screenwriter"
            (plugin / ".codex-plugin").mkdir(parents=True)
            (plugin / ".codex-plugin" / "plugin.json").write_text(
                "{}",
                encoding="utf-8",
            )
            report = check_repo(Path(temp))
            self.assertFalse(report.ok)
            self.assertTrue(
                any("plugin-contract.json" in error for error in report.errors)
            )

    def test_installed_and_contract_skills_must_match(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plugin = write_plugin(root, ["picturebook-screenwriter"])
            extra = plugin / "skills" / "extra"
            extra.mkdir()
            (extra / "SKILL.md").write_text("# extra", encoding="utf-8")
            report = check_repo(root)
            self.assertFalse(report.ok)
            self.assertTrue(
                any("skill set mismatch" in error for error in report.errors)
            )

    def test_valid_local_plugin_passes(self):
        with tempfile.TemporaryDirectory() as temp:
            write_plugin(Path(temp), ["picturebook-screenwriter"])
            report = check_repo(Path(temp))
            self.assertEqual(report.errors, ())
            self.assertTrue(report.ok)


if __name__ == "__main__":
    unittest.main()
```

- [x] **Step 2: Run the test and confirm it fails**

Run: `python .\scripts\test_governance_check.py -v`

Expected: ERROR because `governance_check.py` does not exist.

- [x] **Step 3: Implement the checker**

Create `scripts/governance_check.py`:

```python
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
```

- [x] **Step 4: Run tests and CLI**

```powershell
python .\scripts\test_governance_check.py -v
python .\scripts\governance_check.py
```

Expected: 4 tests pass and the CLI exits 0 on the real repository.

- [x] **Step 5: Commit**

```powershell
git add .\scripts\governance_check.py .\scripts\test_governance_check.py
git commit -m "feat: add governance checker"
```

---

### Task 5: Add package inventory validation

**Files:**
- Create: `scripts/package_check.py`
- Test: `scripts/test_package_check.py`

**Interfaces:**
- Produces:
  - `PackageReport(ok: bool, version: str, file_count: int, files: tuple[str, ...], errors: tuple[str, ...])`
  - `build_package_report(root: Path) -> PackageReport`
  - CLI: `python scripts/package_check.py`

- [x] **Step 1: Write failing package tests**

Create `scripts/test_package_check.py`:

```python
import json
import tempfile
import unittest
from pathlib import Path

from package_check import build_package_report


ROOT = Path(__file__).resolve().parents[1]


class PackageCheckTests(unittest.TestCase):
    def test_real_repo_report_is_valid(self):
        report = build_package_report(ROOT)
        self.assertEqual(report.version, "0.3.0")
        self.assertIn(".codex-plugin/plugin.json", report.files)
        self.assertIn("config/plugin-contract.json", report.files)
        self.assertTrue(report.ok, report.errors)

    def test_forbidden_credential_file_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            plugin = Path(temp) / "plugins" / "picturebook-screenwriter"
            (plugin / ".codex-plugin").mkdir(parents=True)
            (plugin / ".codex-plugin" / "plugin.json").write_text(
                json.dumps({"version": "0.3.0"}),
                encoding="utf-8",
            )
            (plugin / ".env").write_text("SHOULD_NOT_EXIST=1", encoding="utf-8")
            report = build_package_report(Path(temp))
            self.assertFalse(report.ok)
            self.assertTrue(any(".env" in error for error in report.errors))

    def test_generated_directories_are_excluded(self):
        with tempfile.TemporaryDirectory() as temp:
            plugin = Path(temp) / "plugins" / "picturebook-screenwriter"
            (plugin / ".codex-plugin").mkdir(parents=True)
            (plugin / ".codex-plugin" / "plugin.json").write_text(
                json.dumps({"version": "0.3.0"}),
                encoding="utf-8",
            )
            cache = plugin / "skills" / "demo" / "__pycache__"
            cache.mkdir(parents=True)
            (cache / "demo.pyc").write_bytes(b"cache")
            report = build_package_report(Path(temp))
            self.assertFalse(any("__pycache__" in file for file in report.files))


if __name__ == "__main__":
    unittest.main()
```

- [x] **Step 2: Run the test and confirm it fails**

Run: `python .\scripts\test_package_check.py -v`

Expected: ERROR because `package_check.py` does not exist.

- [x] **Step 3: Implement package validation**

Create `scripts/package_check.py`:

```python
"""Offline package inventory and credential-path validation."""

import json
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PackageReport:
    ok: bool
    version: str
    file_count: int
    files: tuple[str, ...]
    errors: tuple[str, ...]


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
```

- [x] **Step 4: Run tests and CLI**

```powershell
python .\scripts\test_package_check.py -v
python .\scripts\package_check.py
```

Expected: 3 tests pass and the CLI exits 0 on the real repository.

- [x] **Step 5: Commit**

```powershell
git add .\scripts\package_check.py .\scripts\test_package_check.py
git commit -m "feat: add package inventory validation"
```

---

### Task 6: Add the human release checklist and local commands

**Files:**
- Create: `docs/RELEASE.md`
- Modify: `README.md`
- Modify: `plugins/picturebook-screenwriter/README.md`
- Test: `plugins/picturebook-screenwriter/tests/test_release_contract.py`

**Interfaces:**
- Produces: one repeatable release gate covering offline checks, live checks, versioning, and marketplace installation.

- [x] **Step 1: Add failing checklist tests**

Append to `plugins/picturebook-screenwriter/tests/test_release_contract.py`:

```python
class ReleaseChecklistTests(unittest.TestCase):
    def test_release_checklist_names_all_gates(self):
        text = (ROOT.parent.parent / "docs" / "RELEASE.md").read_text(encoding="utf-8")
        for gate in (
            "test_*.py",
            "generate.test.js",
            "run_regression.js",
            "governance_check.py",
            "package_check.py",
            "validate_plugin.py",
            "live Feishu",
            "marketplace",
        ):
            with self.subTest(gate=gate):
                self.assertIn(gate, text)

    def test_readme_documents_governance_commands(self):
        text = (ROOT.parent.parent / "README.md").read_text(encoding="utf-8")
        self.assertIn("python .\\scripts\\governance_check.py", text)
        self.assertIn("python .\\scripts\\package_check.py", text)
```

- [x] **Step 2: Run the test and confirm it fails**

Run: `python .\plugins\picturebook-screenwriter\tests\test_release_contract.py -v`

Expected: FAIL because `docs/RELEASE.md` and governance commands are not documented.

- [x] **Step 3: Create the release checklist**

Create `docs/RELEASE.md`:

````markdown
# Release Checklist

## Offline gates

1. Run every plugin Python test:

   ```powershell
   Get-ChildItem -LiteralPath .\plugins\picturebook-screenwriter -Recurse -Filter test_*.py |
     ForEach-Object { python $_.FullName; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE } }
   ```

2. Run the image-generate Node test:

   ```powershell
   node --test .\plugins\picturebook-screenwriter\skills\image-generate\generate.test.js
   ```

3. Run the staging regression:

   ```powershell
   node .\plugins\picturebook-screenwriter\skills\staging-planner\scripts\run_regression.js
   ```

4. Run plugin structure validation:

   ```powershell
   python C:\Users\lvan\.codex\skills\.system\plugin-creator\scripts\validate_plugin.py .\plugins\picturebook-screenwriter
   ```

5. Run governance and package checks:

   ```powershell
   python .\scripts\governance_check.py
   python .\scripts\package_check.py
   ```

6. Confirm `docs/CHANGELOG.md` and `0.3.0` describe this release.
7. Confirm no credentials, run directories, or `.picturebook-screenwriter` files are committed.

## Explicit live gates

1. Run the documented multi-user live Feishu acceptance only after both users approve.
2. Run live image generation only with explicit user confirmation, an explicit output directory, explicit size and quality, and a supplied API key.
3. Confirm no key appears in command history, logs, metadata, or exports.
4. Install from the published Git ref in a clean Codex profile.
5. Record acceptance results and blockers before publishing to the marketplace.
````

- [x] **Step 4: Document local governance commands**

Add a “Release governance” section to root `README.md`:

```markdown
## Release governance

```powershell
python .\scripts\governance_check.py
python .\scripts\package_check.py
```

See `docs/RELEASE.md` before publishing.
```

Add the same two commands to the plugin README verification section.

- [x] **Step 5: Run release and structure checks**

```powershell
python .\plugins\picturebook-screenwriter\tests\test_release_contract.py -v
python .\scripts\governance_check.py
python .\scripts\package_check.py
python C:\Users\lvan\.codex\skills\.system\plugin-creator\scripts\validate_plugin.py .\plugins\picturebook-screenwriter
```

Expected: all checks pass.

- [x] **Step 6: Commit**

```powershell
git add docs\RELEASE.md README.md .\plugins\picturebook-screenwriter\README.md .\plugins\picturebook-screenwriter\tests\test_release_contract.py
git commit -m "docs: add release checklist"
```

---

### Task 7: Add explicit multi-user live acceptance

**Files:**
- Create: `docs/superpowers/plans/2026-09-18-06-live-acceptance.md`
- Test: `plugins/picturebook-screenwriter/tests/test_release_contract.py`

**Interfaces:**
- Produces: a manual acceptance script for two Feishu users, with offline and live boundaries clearly separated.

- [x] **Step 1: Add the failing documentation test**

Append to `plugins/picturebook-screenwriter/tests/test_release_contract.py`:

```python
class LiveAcceptanceTests(unittest.TestCase):
    def test_live_acceptance_names_required_evidence(self):
        path = (
            ROOT.parent.parent
            / "docs"
            / "superpowers"
            / "plans"
            / "2026-09-18-06-live-acceptance.md"
        )
        text = path.read_text(encoding="utf-8")
        for evidence in (
            "User A",
            "User B",
            "revision_id",
            "human edit survives",
            "only one lease",
            "explicit user approval",
        ):
            with self.subTest(evidence=evidence):
                self.assertIn(evidence, text)
```

- [x] **Step 2: Run the test and confirm it fails**

Run: `python .\plugins\picturebook-screenwriter\tests\test_release_contract.py -v`

Expected: FAIL/ERROR because the live acceptance document does not exist.

- [x] **Step 3: Write the live acceptance document**

Create `docs/superpowers/plans/2026-09-18-06-live-acceptance.md`:

```markdown
# Live Multi-User Acceptance

This document is a manual gate. It is not part of the offline test suite and must not run automatically.

## Preconditions

1. All offline release gates pass.
2. User A and User B give explicit user approval before any live Feishu check.
3. Each user authenticates with their own `lark-cli` account.
4. The target Feishu Wiki is the only shared authority.

## Feishu acceptance

1. User A runs `preflight`.
2. User A completes `prepare`, `publish`, and `verify`.
3. User B runs `preflight`.
4. User B edits one target Wiki page.
5. User A reruns sync; the human edit survives.
6. Both users load knowledge and confirm the same `revision_id`.
7. User B attempts a concurrent publish; only one lease may win.
8. Record page links, revision IDs, timestamps, and any conflict decisions.

## Optional image acceptance

1. The user explicitly confirms one prompt, output directory, size, and quality.
2. The user supplies the API key for that command only.
3. The generated image and metadata are inspected locally.
4. Confirm that no key is printed, logged, committed, or exported.

## Stop conditions

- A human edit is lost.
- Two publishes change the same page without a lease decision.
- Users receive different authoritative `revision_id` values without an explanation.
- Any credential appears in output.

The release may proceed only after all blockers are resolved and acceptance results are recorded.
```

- [x] **Step 4: Run release tests**

Run: `python .\plugins\picturebook-screenwriter\tests\test_release_contract.py -v`

Expected: all release-contract tests pass.

- [x] **Step 5: Commit**

```powershell
git add docs\superpowers\plans\2026-09-18-06-live-acceptance.md .\plugins\picturebook-screenwriter\tests\test_release_contract.py
git commit -m "docs: add multi-user live acceptance"
```

---

## Self-Review

- Covers the roadmap's Phase 6 deliverables: session export, structural governance, package validation, semantic versioning, release checklist, marketplace gate, and multi-user acceptance.
- Adds the missing public-surface alignment required after Phase 5 instead of relying on stale README text.
- Makes session export fail closed for relative paths, unsafe IDs, plugin-directory destinations, and obvious secret fields.
- Keeps governance and package checks offline and deterministic.
- Keeps Feishu, image API, and marketplace publication as explicit human acceptance gates.
- No task depends on live credentials being available during development.
