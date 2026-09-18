# Plugin Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Define and enforce the plugin's public contract before more WorkBuddy features are ported.

**Architecture:** Add a declarative contract at the plugin root, mirror it in the entry skill, and enforce it with contract tests. The contract becomes the single source for skill inventory, workflow intents, artifact types, write policy, permissions, and unsupported WorkBuddy capabilities.

**Tech Stack:** Python 3.10+ standard library, `unittest`, JSON, Markdown, Codex plugin manifest.

**Spec:** `docs/superpowers/specs/2026-09-18-workbuddy-codex-plugin-roadmap.md`

## Global Constraints

- User-visible plugin text is Chinese.
- The plugin must not claim WorkBuddy-only TeamCreate, SendMessage, native hooks, or multi-subagent capabilities.
- No file may be written without explicit user approval or an explicit export request.
- Every contract rule must be testable without a live Feishu or image API.
- The contract must not describe WorkBuddy's runtime as a dependency.

---

### Task 1: Create the plugin contract document

**Files:**
- Create: `plugins/picturebook-screenwriter/config/plugin-contract.json`
- Test: `plugins/picturebook-screenwriter/tests/test_plugin_contract.py`

**Interfaces:**
- Produces: `PluginContract` JSON with `schema_version`, `entry_skill`, `skills`, `intents`, `artifact_types`, `workflow_states`, `write_policy`, and `unsupported_workbuddy_capabilities`.

- [ ] **Step 1: Write the failing contract test**

```python
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
CONTRACT = ROOT / "config" / "plugin-contract.json"


class PluginContractTests(unittest.TestCase):
    def test_contract_schema_is_declared(self):
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        self.assertEqual(contract["schema_version"], 1)
        self.assertEqual(contract["entry_skill"], "picturebook-screenwriter")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test and verify failure**

Run:

```powershell
python .\plugins\picturebook-screenwriter\tests\test_plugin_contract.py -v
```

Expected: `FileNotFoundError` for `config/plugin-contract.json`.

- [ ] **Step 3: Create the contract**

```json
{
  "schema_version": 1,
  "entry_skill": "picturebook-screenwriter",
  "skills": [
    "picturebook-screenwriter",
    "text-craft",
    "craft-benchmark-check",
    "staging-planner",
    "wiki-ingest",
    "feishu-knowledge-store",
    "knowledge-loader"
  ],
  "intents": [
    "creation",
    "revision",
    "review",
    "knowledge",
    "illustration"
  ],
  "artifact_types": [
    "positioning",
    "topic",
    "worldview",
    "character",
    "outline",
    "script"
  ],
  "workflow_states": [
    "briefing",
    "knowledge_loading",
    "drafting",
    "self_review",
    "quality_review",
    "confirmation",
    "approved",
    "exported",
    "rejected"
  ],
  "write_policy": {
    "default": "dialog_only",
    "allowed_write_triggers": [
      "user_approves_confirmation_gate",
      "user_explicitly_requests_export"
    ],
    "forbidden_write_triggers": [
      "workflow_finished",
      "quality_gate_passed",
      "cache_available",
      "plugin_maintenance"
    ]
  },
  "unsupported_workbuddy_capabilities": [
    "TeamCreate",
    "SendMessage",
    "native_workbuddy_hooks",
    "multi_subagent_team_runtime",
    "workbuddy_local_library"
  ]
}
```

- [ ] **Step 4: Run the test and verify success**

Run:

```powershell
python .\plugins\picturebook-screenwriter\tests\test_plugin_contract.py -v
```

Expected: 1 test passes.

- [ ] **Step 5: Commit**

```powershell
git add .\plugins\picturebook-screenwriter\config\plugin-contract.json .\plugins\picturebook-screenwriter\tests\test_plugin_contract.py
git commit -m "feat: define plugin contract"
```

---

### Task 2: Keep the contract synchronized with real skill directories

**Files:**
- Modify: `plugins/picturebook-screenwriter/tests/test_plugin_contract.py`

**Interfaces:**
- Consumes: `config/plugin-contract.json`.
- Produces: a test that fails when a skill directory is missing, undeclared, or stale.

- [ ] **Step 1: Add the inventory test**

```python
    def test_contract_matches_installed_skills(self):
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        installed = {
            path.name for path in (ROOT / "skills").iterdir()
            if path.is_dir() and (path / "SKILL.md").exists()
        }
        self.assertEqual(set(contract["skills"]), installed)
```

- [ ] **Step 2: Run the test**

```powershell
python .\plugins\picturebook-screenwriter\tests\test_plugin_contract.py -v
```

Expected: both tests pass. If a skill directory is missing or undeclared, fix the JSON rather than deleting the skill.

- [ ] **Step 3: Commit**

```powershell
git add .\plugins\picturebook-screenwriter\tests\test_plugin_contract.py
git commit -m "test: enforce plugin skill inventory"
```

---

### Task 3: Contract the entry workflow and intent routes

**Files:**
- Modify: `plugins/picturebook-screenwriter/skills/picturebook-screenwriter/SKILL.md`
- Modify: `plugins/picturebook-screenwriter/tests/test_plugin_contract.py`

**Interfaces:**
- Consumes: contract `intents` and `workflow_states`.
- Produces: an entry skill that maps every intent to a deterministic next step.

- [ ] **Step 1: Add workflow tests**

```python
ENTRY_SKILL = ROOT / "skills" / "picturebook-screenwriter" / "SKILL.md"

    def test_entry_skill_declares_every_intent(self):
        text = ENTRY_SKILL.read_text(encoding="utf-8")
        for intent in ("creation", "revision", "review", "knowledge", "illustration"):
            self.assertIn(intent, text)

    def test_entry_skill_declares_confirmation_and_export_gates(self):
        text = ENTRY_SKILL.read_text(encoding="utf-8")
        self.assertIn("确认门", text)
        self.assertIn("明确要求导出", text)
```

- [ ] **Step 2: Update the entry workflow**

Add this section to `skills/picturebook-screenwriter/SKILL.md`:

```markdown
## Intent Routes

- `creation`: enter `briefing`, then `knowledge_loading`, `drafting`, `self_review`, `quality_review`, and `confirmation`.
- `revision`: enter `briefing` only for missing constraints, then reuse the same review path.
- `review`: skip drafting and run `craft-benchmark-check` plus the applicable quality gate.
- `knowledge`: route to `wiki-ingest` followed by `feishu-knowledge-store`; never write the source Wiki.
- `illustration`: require an approved or explicitly supplied script, then route to `staging-planner` before prompt construction.

The plugin does not implement WorkBuddy TeamCreate, SendMessage, native hooks, or a multi-subagent runtime. It represents those editorial roles internally.
```

- [ ] **Step 3: Run the tests**

```powershell
python .\plugins\picturebook-screenwriter\tests\test_plugin_contract.py -v
```

Expected: 4 tests pass.

- [ ] **Step 4: Commit**

```powershell
git add .\plugins\picturebook-screenwriter\skills\picturebook-screenwriter\SKILL.md .\plugins\picturebook-screenwriter\tests\test_plugin_contract.py
git commit -m "feat: contract entry workflow routes"
```

---

### Task 4: Make write policy machine-checkable

**Files:**
- Modify: `plugins/picturebook-screenwriter/tests/test_plugin_contract.py`
- Modify: `plugins/picturebook-screenwriter/skills/picturebook-screenwriter/SKILL.md`

**Interfaces:**
- Consumes: `write_policy` from the contract.
- Produces: an entry-skill gate that blocks implicit saving.

- [ ] **Step 1: Add the write-policy test**

```python
    def test_entry_skill_forbids_implicit_file_writes(self):
        text = ENTRY_SKILL.read_text(encoding="utf-8")
        self.assertIn("不得默认落盘", text)
        self.assertIn("用户明确批准", text)
```

- [ ] **Step 2: Add the write gate**

```markdown
## Write Gate

产物默认只在对话中呈现，不得默认落盘。只有用户明确批准确认门，或明确要求导出时，才允许写入工作区文件。写文件前必须说明目标路径、文件名和版本号。
```

- [ ] **Step 3: Run the tests**

```powershell
python .\plugins\picturebook-screenwriter\tests\test_plugin_contract.py -v
```

Expected: 5 tests pass.

- [ ] **Step 4: Commit**

```powershell
git add .\plugins\picturebook-screenwriter\skills\picturebook-screenwriter\SKILL.md .\plugins\picturebook-screenwriter\tests\test_plugin_contract.py
git commit -m "feat: enforce explicit write gate"
```

---

### Task 5: Publish the contract in plugin metadata and README

**Files:**
- Modify: `plugins/picturebook-screenwriter/.codex-plugin/plugin.json`
- Modify: `plugins/picturebook-screenwriter/README.md`
- Modify: `README.md`
- Test: `plugins/picturebook-screenwriter/tests/test_plugin_contract.py`

**Interfaces:**
- Consumes: the final contract.
- Produces: user-facing capability claims that match implementation.

- [ ] **Step 1: Add metadata test**

```python
MANIFEST = ROOT / ".codex-plugin" / "plugin.json"

    def test_manifest_matches_contract_boundary(self):
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.assertIn("多人协作飞书知识库同步与权威检索", manifest["interface"]["longDescription"])
        self.assertNotIn("WorkBuddy TeamCreate", manifest["interface"]["longDescription"])
```

- [ ] **Step 2: Update user-facing copy**

Use these claims in both README files:

```markdown
本插件是 Codex 原生的绘本编辑部工作流，不依赖 WorkBuddy 团队运行时。当前支持编辑式入口、飞书权威知识库、文字工艺、量化自检和分镜规划；后续阶段会逐步补齐完整创作、质检、插画和导出能力。
```

- [ ] **Step 3: Validate the plugin**

Run with the bundled Codex Python because the system Python may lack `yaml`:

```powershell
C:\Users\lvan\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe C:\Users\lvan\.codex\skills\.system\plugin-creator\scripts\validate_plugin.py .\plugins\picturebook-screenwriter
```

Expected: `Plugin validation passed`.

- [ ] **Step 4: Run all contract tests**

```powershell
python .\plugins\picturebook-screenwriter\tests\test_plugin_contract.py -v
python .\plugins\picturebook-screenwriter\tests\test_skill_contract.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```powershell
git add .\plugins\picturebook-screenwriter\.codex-plugin\plugin.json .\plugins\picturebook-screenwriter\README.md README.md .\plugins\picturebook-screenwriter\tests\test_plugin_contract.py
git commit -m "docs: publish plugin contract boundary"
```

---

## Self-Review

- Contract covers skill inventory, intents, artifact types, workflow states, write policy, and unsupported WorkBuddy capabilities.
- No task depends on a later phase.
- Every task has a deterministic offline test.
- The only packaging command uses the bundled Python runtime that is already available in this workspace.
