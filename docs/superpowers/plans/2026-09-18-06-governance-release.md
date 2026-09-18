# Governance and Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the plugin auditable, self-checking, versioned, installable, and releasable.

**Architecture:** Port the session-export concept into a Codex-local audit bundle, add a repo-level governance script, create a release checklist, and make packaging validation part of every release. Live multi-user acceptance is separated from offline unit tests.

**Tech Stack:** Python 3.10+ standard library, `unittest`, JSON, Markdown, Git, Codex plugin packaging.

**Spec:** `docs/superpowers/specs/2026-09-18-workbuddy-codex-plugin-roadmap.md`

## Global Constraints

- Session export must not write into the plugin installation directory.
- Export paths must be explicit.
- Governance checks must be deterministic and offline.
- Release versioning follows semantic versioning.
- Every release must pass plugin validation and all unit/regression suites.
- Live Feishu and image API checks require explicit user participation.
- No credentials may be included in logs, exports, or git history.

---

### Task 1: Port the session export skill

**Files:**
- Create: `plugins/picturebook-screenwriter/skills/session-export/SKILL.md`
- Create: `plugins/picturebook-screenwriter/skills/session-export/scripts/export_session.py`
- Test: `plugins/picturebook-screenwriter/skills/session-export/scripts/test_export_session.py`

**Interfaces:**
- Produces:
  - `build_manifest(session_id, messages, files, images) -> dict`
  - `export_session(session_id, messages, files, images, output_dir) -> Path`
- Consumes: already captured JSON event arrays; it must not invent missing data.

- [ ] **Step 1: Write failing tests**

```python
import tempfile
import unittest
from pathlib import Path
from export_session import build_manifest, export_session


class ExportSessionTests(unittest.TestCase):
    def test_manifest_counts_inputs(self):
        manifest = build_manifest("s1", [{"role": "user"}], ["a.md"], ["a.png"])
        self.assertEqual(manifest["summary"]["messages"], 1)
        self.assertEqual(manifest["summary"]["files"], 1)
        self.assertEqual(manifest["summary"]["images"], 1)

    def test_output_dir_is_explicit(self):
        with tempfile.TemporaryDirectory() as temp:
            path = export_session("s1", [], [], [], Path(temp))
            self.assertTrue(path.exists())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Implement the export boundary**

```python
"""Codex-local session audit bundle builder."""

import json
from datetime import datetime, timezone
from pathlib import Path


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


def export_session(session_id: str, messages: list, files: list,
                   images: list, output_dir: Path) -> Path:
    output_dir = Path(output_dir)
    if not str(output_dir.resolve()).startswith(str(Path.cwd().resolve())):
        raise ValueError("output directory must be explicit and outside the plugin")
    bundle = output_dir / session_id
    bundle.mkdir(parents=True, exist_ok=True)
    (bundle / "manifest.json").write_text(
        json.dumps(build_manifest(session_id, messages, files, images),
                   ensure_ascii=False, indent=2), encoding="utf-8")
    return bundle
```

- [ ] **Step 3: Create the skill**

```markdown
---
name: session-export
description: 导出当前会话的对话、产物和审计摘要；输出目录必须由用户显式指定。
---

# Session Export

## Workflow

1. 请求用户确认导出目录。
2. 收集本次会话的对话消息、产物文件和图片路径。
3. 运行 `scripts/export_session.py`。
4. 返回导出目录、消息数、文件数和图片数。

本技能不写入插件目录，不读取或记录密钥。
```

- [ ] **Step 4: Run tests**

```powershell
python .\plugins\picturebook-screenwriter\skills\session-export\scripts\test_export_session.py -v
```

Expected: 2 tests pass.

- [ ] **Step 5: Commit**

```powershell
git add .\plugins\picturebook-screenwriter\skills\session-export
git commit -m "feat: port session export boundary"
```

---

### Task 2: Add the repo governance checker

**Files:**
- Create: `scripts/governance_check.py`
- Test: `scripts/test_governance_check.py`

**Interfaces:**
- Produces:
  - `check_repo(root) -> GovernanceReport`
  - `GovernanceReport(ok, errors, warnings)`
  - CLI: `python scripts/governance_check.py`

- [ ] **Step 1: Write failing tests**

```python
import unittest
from pathlib import Path
from governance_check import check_repo


class GovernanceCheckTests(unittest.TestCase):
    def test_missing_plugin_manifest_is_error(self):
        report = check_repo(Path("does-not-exist"))
        self.assertFalse(report.ok)
        self.assertTrue(any("plugin.json" in error for error in report.errors))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Implement the checker**

```python
"""Offline governance checks for the picture book plugin repository."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GovernanceReport:
    ok: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]


def check_repo(root: Path) -> GovernanceReport:
    errors: list[str] = []
    warnings: list[str] = []
    manifest = root / "plugins" / "picturebook-screenwriter" / ".codex-plugin" / "plugin.json"
    if not manifest.exists():
        errors.append("missing plugin.json")
    contract = root / "plugins" / "picturebook-screenwriter" / "config" / "plugin-contract.json"
    if not contract.exists():
        warnings.append("missing plugin-contract.json")
    return GovernanceReport(not errors, tuple(errors), tuple(warnings))


def main() -> int:
    report = check_repo(Path(__file__).parents[1])
    for error in report.errors:
        print(f"ERROR: {error}")
    for warning in report.warnings:
        print(f"WARN: {warning}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 3: Run tests and CLI**

```powershell
python .\scripts\test_governance_check.py -v
python .\scripts\governance_check.py
```

Expected: test passes and CLI exits 0 after the contract exists.

- [ ] **Step 4: Commit**

```powershell
git add .\scripts\governance_check.py .\scripts\test_governance_check.py
git commit -m "feat: add governance checker"
```

---

### Task 3: Add semantic versioning and release notes

**Files:**
- Create: `docs/CHANGELOG.md`
- Modify: `plugins/picturebook-screenwriter/.codex-plugin/plugin.json`
- Test: `plugins/picturebook-screenwriter/tests/test_release_contract.py`

**Interfaces:**
- Produces:
  - `version` in `plugin.json`
  - `docs/CHANGELOG.md` with Keep a Changelog format.

- [ ] **Step 1: Write the release test**

```python
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]


class ReleaseContractTests(unittest.TestCase):
    def test_version_is_semver(self):
        manifest = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertIsNotNone(re.fullmatch(r"\d+\.\d+\.\d+", manifest["version"]))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Update version and changelog**

Use:

```markdown
# Changelog

## [Unreleased]

### Added

- Codex-native picture-book editorial workflow.
- Feishu authoritative knowledge synchronization and retrieval.
- Craft benchmark and staging planning.

### Changed

- Split the WorkBuddy reference archive from the Codex plugin repository.
```

Set `version` to the next planned release, such as `0.3.0` after Phase 2.

- [ ] **Step 3: Run tests**

```powershell
python .\plugins\picturebook-screenwriter\tests\test_release_contract.py -v
```

Expected: 1 test passes.

- [ ] **Step 4: Commit**

```powershell
git add docs\CHANGELOG.md .\plugins\picturebook-screenwriter\.codex-plugin\plugin.json .\plugins\picturebook-screenwriter\tests\test_release_contract.py
git commit -m "chore: add release versioning"
```

---

### Task 4: Add the release checklist

**Files:**
- Create: `docs/RELEASE.md`
- Test: `plugins/picturebook-screenwriter/tests/test_release_contract.py`

**Interfaces:**
- Produces: a repeatable release gate executed by a human or agent.

- [ ] **Step 1: Add the checklist test**

```python
RELEASE = ROOT.parent.parent / "docs" / "RELEASE.md"

    def test_release_checklist_names_all_gates(self):
        text = RELEASE.read_text(encoding="utf-8")
        for gate in ("plugin validation", "unit tests", "regression", "live Feishu", "marketplace"):
            self.assertIn(gate, text)
```

- [ ] **Step 2: Create the checklist**

```markdown
# Release Checklist

1. Run plugin validation.
2. Run all unit tests.
3. Run staging-planner regression.
4. Run governance check.
5. Confirm live Feishu preflight with a real user account.
6. Confirm marketplace installation from the published Git ref.
7. Confirm no credentials or run directories are committed.
8. Update `docs/CHANGELOG.md` and the plugin version.
```

- [ ] **Step 3: Run tests**

```powershell
python .\plugins\picturebook-screenwriter\tests\test_release_contract.py -v
```

Expected: 2 tests pass.

- [ ] **Step 4: Commit**

```powershell
git add docs\RELEASE.md .\plugins\picturebook-screenwriter\tests\test_release_contract.py
git commit -m "docs: add release checklist"
```

---

### Task 5: Add packaging validation to the release workflow

**Files:**
- Modify: `README.md`
- Modify: `plugins/picturebook-screenwriter/README.md`
- Test: `plugins/picturebook-screenwriter/tests/test_release_contract.py`

**Interfaces:**
- Produces: a documented, reproducible validation command.

- [ ] **Step 1: Add the command test**

```python
    def test_readme_documents_bundled_python_validation(self):
        text = (ROOT.parent.parent / "README.md").read_text(encoding="utf-8")
        self.assertIn("validate_plugin.py", text)
        self.assertIn("codex-primary-runtime", text)
```

- [ ] **Step 2: Document the command**

Add to `README.md`:

```markdown
If system Python lacks `yaml`, run plugin validation with the bundled Codex Python:

```powershell
C:\Users\lvan\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe C:\Users\lvan\.codex\skills\.system\plugin-creator\scripts\validate_plugin.py .\plugins\picturebook-screenwriter
```
```

- [ ] **Step 3: Validate the plugin**

Run the documented command. Expected: `Plugin validation passed`.

- [ ] **Step 4: Commit**

```powershell
git add README.md .\plugins\picturebook-screenwriter\README.md .\plugins\picturebook-screenwriter\tests\test_release_contract.py
git commit -m "docs: document release validation command"
```

---

### Task 6: Add multi-user smoke acceptance

**Files:**
- Create: `docs/superpowers/plans/2026-09-18-06-live-acceptance.md`
- Test: `plugins/picturebook-screenwriter/tests/test_release_contract.py`

**Interfaces:**
- Produces: a live acceptance script for two users.

- [ ] **Step 1: Add the live acceptance test**

```python
    def test_live_acceptance_document_exists(self):
        path = ROOT.parent.parent / "docs" / "superpowers" / "plans" / "2026-09-18-06-live-acceptance.md"
        self.assertTrue(path.exists())
```

- [ ] **Step 2: Write the live acceptance document**

```markdown
# Live Acceptance

1. User A authenticates with `lark-cli auth login`.
2. User A runs Feishu preflight.
3. User A completes `prepare`, `publish`, and `verify`.
4. User B authenticates and repeats preflight.
5. User B edits one target Wiki page.
6. User A reruns sync; the human edit must survive.
7. Both users load knowledge and confirm the same `revision_id`.
8. User B attempts a concurrent publish; only one lease may win.
```

- [ ] **Step 3: Run release tests**

```powershell
python .\plugins\picturebook-screenwriter\tests\test_release_contract.py -v
```

Expected: 4 tests pass.

- [ ] **Step 4: Commit**

```powershell
git add docs\superpowers\plans\2026-09-18-06-live-acceptance.md .\plugins\picturebook-screenwriter\tests\test_release_contract.py
git commit -m "docs: add multi-user live acceptance"
```

---

## Self-Review

- Covers session export, governance, versioning, release checklist, packaging validation, and live acceptance.
- All offline checks are deterministic.
- Live checks are clearly separated from unit tests.
- No task depends on a live Feishu or image API being available during development.
