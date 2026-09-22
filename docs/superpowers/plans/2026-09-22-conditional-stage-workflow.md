# Conditional Stage Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the stage scheduler from an unconditional DAG sorter into a conditional workflow that separates approval from revision, reruns all required checks after revision, and never persists before approval.

**Architecture:** Each creation or revision round is one run manifest. A run stays acyclic; `confirmation_gate` emits a structured outcome and either activates persistence or ends the run as `revision_requested`. The lead creates a new revision run with incremented iteration, full preflight/collision/QA revalidation, and another confirmation gate.

**Tech Stack:** Python 3.12+ standard library, `unittest`, JSON run manifests, existing Codex multi-agent adapter, no new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-22-conditional-stage-workflow-design.md`

## Global Constraints

- `pb-stage-run-v2` is the only executable run schema.
- v1 manifests require explicit migration and must never execute through a fallback path.
- Confirmation outcomes are exactly `approved`, `revision_requested`, and `cancelled`.
- `persistence` may become ready only after `confirmation_gate` has `status=done` and `outcome=approved`.
- `revision_requested` must skip `persistence` and `knowledge_reminder`.
- Every revision run must rerun `preflight`, `collision_check`, `qa`, and `qa_synthesis`.
- A revision run must stop at a new `confirmation_gate`.
- `skipped` is a terminal, non-failure state.
- `cancelled` ends the run and is not equivalent to a skipped conditional branch.
- Task Envelope remains `pb-stage-task-v1`; Result Envelope remains `pb-stage-result-v1`.
- Confirmation gates are lead-owned and must not be dispatched to subagents.
- No production artifact may be written before approval or an explicit export request.
- Preserve existing output path isolation, task validation, and non-revision parallel behavior.

## Review Focus

- A confirmation gate without an outcome must fail closed and leave persistence inactive.
- A skipped branch must not stop unrelated stages or the whole run.
- A revision run must not reuse stale preflight, collision, or QA results.
- Ambiguous v1 manifests must be rejected rather than guessed.
- Multi-agent planning and sequential fallback must produce the same business order.

---

## File Map

- `plugins/picturebook-screenwriter/scripts/stage_dag.py`
  - Owns schema validation, migration, state transitions, condition decisions, templates, and run finalization.
- `plugins/picturebook-screenwriter/scripts/stage_dag_codex.py`
  - Owns current-batch planning, lead actions, confirmation decisions, task envelopes, and sequential fallback.
- `plugins/picturebook-screenwriter/tests/test_stage_dag.py`
  - Owns schema, migration, state machine, template, revision-run, and integration tests.
- `plugins/picturebook-screenwriter/tests/test_stage_dag_codex.py`
  - Owns adapter and fallback behavior.
- `plugins/picturebook-screenwriter/skills/stage-orchestration/SKILL.md`
  - Documents current-batch dispatch and lead-owned outcomes.
- `plugins/picturebook-screenwriter/skills/picturebook-screenwriter/SKILL.md`
  - Documents the user-facing approval and revision round behavior.
- `plugins/picturebook-screenwriter/config/plugin-contract.json`
  - Declares the v2 workflow contract.
- `plugins/picturebook-screenwriter/tests/test_plugin_contract.py`
  - Locks the contract declarations.
- `plugins/picturebook-screenwriter/tests/test_skill_contract.py`
  - Locks the entry-skill routing language.

---

### Task 1: Add v2 Manifest Schema and Explicit v1 Migration

**Files:**
- Modify: `plugins/picturebook-screenwriter/scripts/stage_dag.py`
- Test: `plugins/picturebook-screenwriter/tests/test_stage_dag.py`

**Interfaces:**
- Produces: `RUN_SCHEMA_V1`, `RUN_SCHEMA`, `RUN_STATUSES`, `RUN_OUTCOMES`
- Produces: `migrate_manifest_v1(manifest: dict) -> dict`
- Produces: `validate_manifest(manifest: dict) -> dict` for v2 only
- Produces CLI action: `--action migrate`

- [ ] **Step 1: Convert the test fixture to a v2 manifest and add migration tests**

Replace the top-level helper in `test_stage_dag.py` with:

```python
def _manifest(**overrides):
    stages = [
        {
            "stage_id": "session_init",
            "assignee": "pb-intake-agent",
            "depends_on": [],
            "status": "pending",
            "gate": "none",
            "outcome": None,
            "when": None,
            "input_refs": [],
            "output_refs": [],
            "skip_reason": None,
            "blocked_reason": None,
        },
        {
            "stage_id": "brief_gate",
            "assignee": "pb-intake-agent",
            "depends_on": ["session_init"],
            "status": "pending",
            "gate": "brief",
            "outcome": None,
            "when": None,
            "input_refs": [],
            "output_refs": [],
            "skip_reason": None,
            "blocked_reason": None,
        },
    ]
    manifest = {
        "schema_version": "pb-stage-run-v2",
        "run_id": "20260922-example-0001",
        "root_run_id": "20260922-example-0001",
        "revision_of_run_id": None,
        "iteration": 1,
        "intent": "creation",
        "artifact_type": "script",
        "mode": "full",
        "project_root": "E:/workspace/example-project",
        "status": "pending",
        "outcome": None,
        "revision_feedback": [],
        "source_artifact_ref": None,
        "stages": stages,
    }
    manifest.update(overrides)
    return manifest
```

Add migration tests:

```python
def test_migrate_v1_removes_revision_loop_and_adds_persistence_condition(self):
    legacy = stage_dag._creation_template_manifest_v1()
    migrated = stage_dag.migrate_manifest_v1(legacy)
    stages = {stage["stage_id"]: stage for stage in migrated["stages"]}

    self.assertEqual(migrated["schema_version"], "pb-stage-run-v2")
    self.assertEqual(migrated["root_run_id"], legacy["run_id"])
    self.assertEqual(migrated["iteration"], 1)
    self.assertNotIn("revision_loop", stages)
    self.assertEqual(
        stages["persistence"]["when"],
        {"stage_id": "confirmation_gate", "outcome": "approved"},
    )

def test_migrate_rejects_completed_gate_without_outcome(self):
    legacy = stage_dag._creation_template_manifest_v1()
    gate = next(
        stage for stage in legacy["stages"]
        if stage["stage_id"] == "confirmation_gate"
    )
    gate["status"] = "done"

    with self.assertRaisesRegex(stage_dag.StageDagError, "outcome"):
        stage_dag.migrate_manifest_v1(legacy)
```

- [ ] **Step 2: Run the focused tests to verify failure**

Run:

```powershell
python .\plugins\picturebook-screenwriter\tests\test_stage_dag.py -v
```

Expected: FAIL because v2 fields, `_creation_template_manifest_v1`, and `migrate_manifest_v1` do not exist.

- [ ] **Step 3: Add v2 constants and structural validation**

Add:

```python
RUN_SCHEMA_V1 = "pb-stage-run-v1"
RUN_SCHEMA = "pb-stage-run-v2"

RUN_STATUSES = {
    "pending", "running", "blocked", "completed", "failed", "cancelled",
}
RUN_OUTCOMES = {None, "approved", "revision_requested", "cancelled"}
CONFIRMATION_OUTCOMES = {"approved", "revision_requested", "cancelled"}
TERMINAL_STAGE_STATUSES = {"done", "skipped", "failed", "cancelled"}

STAGE_STATUSES = {
    "pending", "ready", "running", "blocked",
    "done", "skipped", "failed", "cancelled",
}
```

Update `_validate_manifest_structure` to require:

```python
for key in (
    "run_id", "root_run_id", "intent", "artifact_type",
    "mode", "project_root",
):
    _require_nonempty_str(manifest.get(key), key, errors)

iteration = manifest.get("iteration")
if isinstance(iteration, bool) or not isinstance(iteration, int) or iteration < 1:
    errors.append("iteration 必须是 >= 1 的整数")

if manifest.get("status") not in RUN_STATUSES:
    errors.append(f"status 非法：{manifest.get('status')!r}")

if manifest.get("outcome") not in RUN_OUTCOMES:
    errors.append(f"outcome 非法：{manifest.get('outcome')!r}")

for key in ("revision_feedback", "stages"):
    if not isinstance(manifest.get(key), list):
        errors.append(f"{key} 必须是数组")
```

For every stage, validate optional `outcome`, `when`, `skip_reason`, and `blocked_reason`. A `when` object must have exactly:

```python
{"stage_id", "outcome"}
```

Update `_template_manifest()`, `_creation_template_manifest()`, and `_illustration_template_manifest()` to emit all v2 top-level and stage fields so every generated template validates under `pb-stage-run-v2`. Keep the legacy creation stages unchanged in this task; Task 3 removes `revision_loop` and adds the persistence condition.

- [ ] **Step 4: Implement explicit v1 migration**

Keep a private legacy template for migration tests:

```python
def _creation_template_manifest_v1():
    """Return the frozen v1 creation template used only by migration tests."""
```

Implement:

```python
def migrate_manifest_v1(manifest):
    if not isinstance(manifest, dict):
        raise StageDagError("v1 manifest 顶层必须是对象")
    if manifest.get("schema_version") != RUN_SCHEMA_V1:
        raise StageDagError("migrate 只接受 pb-stage-run-v1")

    migrated = copy.deepcopy(manifest)
    stages = migrated.get("stages")
    if not isinstance(stages, list):
        raise StageDagError("v1 stages 必须是数组")

    by_id = {stage.get("stage_id"): stage for stage in stages}
    gate = by_id.get("confirmation_gate")
    if gate and gate.get("status") == "done" and not gate.get("outcome"):
        raise StageDagError("确认门已 done 但缺少 outcome，无法安全迁移")

    revision = by_id.get("revision_loop")
    persistence = by_id.get("persistence")
    if revision and persistence:
        revision_started = revision.get("status") not in {"pending", "cancelled"}
        persistence_started = persistence.get("status") not in {"pending", "cancelled"}
        if revision_started or persistence_started:
            raise StageDagError("v1 revision_loop/persistence 状态存在歧义")

    migrated["schema_version"] = RUN_SCHEMA
    migrated["root_run_id"] = manifest["run_id"]
    migrated["revision_of_run_id"] = None
    migrated["iteration"] = 1
    migrated["status"] = "pending"
    migrated["outcome"] = None
    migrated["revision_feedback"] = []
    migrated["source_artifact_ref"] = None
    migrated["stages"] = [
        stage for stage in stages
        if stage.get("stage_id") != "revision_loop"
    ]
    for stage in migrated["stages"]:
        stage.setdefault("outcome", None)
        stage.setdefault("when", None)
        stage.setdefault("skip_reason", None)
        stage.setdefault("blocked_reason", None)
    persistence = next(
        stage for stage in migrated["stages"]
        if stage["stage_id"] == "persistence"
    )
    persistence["when"] = {
        "stage_id": "confirmation_gate",
        "outcome": "approved",
    }
    return validate_manifest(migrated)
```

- [ ] **Step 5: Add the migrate CLI action**

Add `"migrate"` to CLI choices and handle it before `validate_manifest`:

```python
if args.action == "migrate":
    print(json.dumps(
        migrate_manifest_v1(manifest),
        ensure_ascii=False,
        indent=2,
    ))
    return 0
```

Direct `validate` on v1 must fail with a message directing the caller to `--action migrate`.

- [ ] **Step 6: Run focused tests**

Run:

```powershell
python .\plugins\picturebook-screenwriter\tests\test_stage_dag.py -v
```

Expected: PASS for schema and migration tests.

- [ ] **Step 7: Commit**

```powershell
git add plugins/picturebook-screenwriter/scripts/stage_dag.py plugins/picturebook-screenwriter/tests/test_stage_dag.py
git commit -m "feat(dag): add v2 manifest migration"
```

---

### Task 2: Add Conditional Decisions, Skipped State, and Run Finalization

**Files:**
- Modify: `plugins/picturebook-screenwriter/scripts/stage_dag.py`
- Test: `plugins/picturebook-screenwriter/tests/test_stage_dag.py`

**Interfaces:**
- Consumes: `RUN_SCHEMA`, `CONFIRMATION_OUTCOMES`, `TERMINAL_STAGE_STATUSES`
- Produces: `StageDecision`
- Produces: `resolve_stage_decisions(manifest) -> list[StageDecision]`
- Produces: `next_batches(manifest) -> list[list[str]]`
- Produces: `finalize_run(manifest, outcome) -> dict`

- [ ] **Step 1: Write failing decision and finalization tests**

Add a focused test helper:

```python
class StageDagConditionalTest(unittest.TestCase):
    @staticmethod
    def _complete_confirmation(manifest, outcome):
        manifest = stage_dag.transition_stage(
            manifest,
            "confirmation_gate",
            "ready",
        )
        manifest = stage_dag.transition_stage(
            manifest,
            "confirmation_gate",
            "running",
        )
        return stage_dag.transition_stage(
            manifest,
            "confirmation_gate",
            "done",
            outcome=outcome,
        )

    @staticmethod
    def _conditional_manifest():
        return _manifest(stages=[
            {
                "stage_id": "confirmation_gate",
                "assignee": "picturebook-screenwriter-team-lead",
                "depends_on": [],
                "status": "pending",
                "gate": "confirmation",
                "outcome": None,
                "when": None,
                "input_refs": [],
                "output_refs": [],
                "skip_reason": None,
                "blocked_reason": None,
            },
            {
                "stage_id": "persistence",
                "assignee": "pb-persistence-agent",
                "depends_on": ["confirmation_gate"],
                "status": "pending",
                "gate": "none",
                "outcome": None,
                "when": {
                    "stage_id": "confirmation_gate",
                    "outcome": "approved",
                },
                "input_refs": [],
                "output_refs": [],
                "skip_reason": None,
                "blocked_reason": None,
            },
            {
                "stage_id": "knowledge_reminder",
                "assignee": "picturebook-screenwriter-team-lead",
                "depends_on": ["persistence"],
                "status": "pending",
                "gate": "none",
                "outcome": None,
                "when": None,
                "input_refs": [],
                "output_refs": [],
                "skip_reason": None,
                "blocked_reason": None,
            },
        ])
```

Add tests:

```python
def test_persistence_is_skipped_when_confirmation_is_revision_requested(self):
    manifest = self._conditional_manifest()
    manifest = self._complete_confirmation(manifest, "revision_requested")

    decisions = {
        item.stage_id: item
        for item in stage_dag.resolve_stage_decisions(manifest)
    }
    self.assertEqual(decisions["persistence"].decision, "skip")

def test_persistence_is_ready_only_after_approved(self):
    manifest = self._conditional_manifest()
    manifest = self._complete_confirmation(manifest, "approved")
    self.assertEqual(stage_dag.ready_stages(manifest), ["persistence"])

def test_finalize_revision_request_skips_downstream(self):
    manifest = self._conditional_manifest()
    manifest = self._complete_confirmation(manifest, "revision_requested")
    manifest = stage_dag.finalize_run(manifest, "revision_requested")
    stages = {stage["stage_id"]: stage for stage in manifest["stages"]}

    self.assertEqual(manifest["status"], "completed")
    self.assertEqual(manifest["outcome"], "revision_requested")
    self.assertEqual(stages["persistence"]["status"], "skipped")
    self.assertEqual(stages["knowledge_reminder"]["status"], "skipped")
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
python .\plugins\picturebook-screenwriter\tests\test_stage_dag.py -v
```

Expected: FAIL because conditional decisions and finalization are missing.

- [ ] **Step 3: Add StageDecision and decision evaluation**

Add:

```python
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class StageDecision:
    stage_id: str
    decision: Literal["waiting", "ready", "skip", "block"]
    reason: str
```

Implement `resolve_stage_decisions` with this exact order:

1. Block on any dependency in `failed`, `blocked`, or `cancelled`.
2. Wait on any dependency in `pending`, `ready`, or `running`.
3. Evaluate `when` for conditions.
4. Skip when the condition outcome does not match.
5. Ready when no `when` exists and all dependencies are `done`.
6. Skip when no `when` exists and any dependency is `skipped`.
7. Wait otherwise.

Only stages with `status in {"pending", "ready"}` produce decisions.

- [ ] **Step 4: Extend transitions**

Add:

```python
"pending": {"ready", "running", "blocked", "failed", "cancelled", "skipped"},
"ready": {"running", "blocked", "failed", "cancelled", "skipped"},
"skipped": set(),
```

Enforce:

```python
if status == "skipped" and not fields.get("skip_reason"):
    raise StageDagError("skipped 阶段必须提供 skip_reason")

if status == "blocked" and not fields.get("blocked_reason"):
    raise StageDagError("blocked 阶段必须提供 blocked_reason")

if status == "done" and current_stage["gate"] == "confirmation":
    outcome = fields.get("outcome")
    if outcome not in CONFIRMATION_OUTCOMES:
        raise StageDagError(f"confirmation outcome 非法：{outcome!r}")

if status == "done" and current_stage["gate"] != "confirmation":
    if fields.get("outcome") is not None:
        raise StageDagError("非 confirmation 阶段不得写入 outcome")
```

Update existing tests that transition a stage to `blocked` so they pass an explicit `blocked_reason`.

- [ ] **Step 5: Implement next_batches and finalize_run**

```python
def next_batches(manifest):
    validated = validate_manifest(manifest)
    ready = [
        item.stage_id
        for item in resolve_stage_decisions(validated)
        if item.decision == "ready"
    ]
    return [sorted(ready)] if ready else []
```

```python
def finalize_run(manifest, outcome):
    validated = validate_manifest(manifest)
    if outcome not in CONFIRMATION_OUTCOMES:
        raise StageDagError(f"非法 run outcome：{outcome!r}")

    if outcome == "approved":
        persistence = next(
            stage for stage in validated["stages"]
            if stage["stage_id"] == "persistence"
        )
        reminder = next(
            stage for stage in validated["stages"]
            if stage["stage_id"] == "knowledge_reminder"
        )
        if persistence["status"] != "done":
            raise StageDagError("persistence 未完成，run 不能标记 approved")
        if reminder["status"] != "done":
            raise StageDagError("knowledge_reminder 未完成，run 不能标记 approved")
        validated["status"] = "completed"
        validated["outcome"] = "approved"
    elif outcome == "revision_requested":
        for stage_id in ("persistence", "knowledge_reminder"):
            stage = next(
                item for item in validated["stages"]
                if item["stage_id"] == stage_id
            )
            if stage["status"] in {"pending", "ready"}:
                stage["status"] = "skipped"
                stage["skip_reason"] = "confirmation_requested_revision"
            elif stage["status"] != "skipped":
                raise StageDagError(
                    f"{stage_id} 状态无法安全收敛为 skipped"
                )
        validated["status"] = "completed"
        validated["outcome"] = "revision_requested"
    else:
        validated["status"] = "cancelled"
        validated["outcome"] = "cancelled"
    return validate_manifest(validated)
```

Keep `parallel_batches()` only for manifests without `when` conditions. If a condition is present, raise:

```python
raise StageDagError(
    "parallel_batches 不支持条件阶段；请使用 next_batches"
)
```

Conditional tests must use `resolve_stage_decisions()` or `next_batches()`.

- [ ] **Step 6: Run focused tests**

```powershell
python .\plugins\picturebook-screenwriter\tests\test_stage_dag.py -v
```

Expected: PASS for decision, transition, and finalization tests.

- [ ] **Step 7: Commit**

```powershell
git add plugins/picturebook-screenwriter/scripts/stage_dag.py plugins/picturebook-screenwriter/tests/test_stage_dag.py
git commit -m "feat(dag): add conditional decisions and run finalization"
```

---

### Task 3: Replace Creation Revision Loop and Add Revision Run Builder

**Files:**
- Modify: `plugins/picturebook-screenwriter/scripts/stage_dag.py`
- Test: `plugins/picturebook-screenwriter/tests/test_stage_dag.py`

**Interfaces:**
- Produces: `_revision_template_manifest() -> dict`
- Produces: `build_revision_manifest(parent_run, feedback, artifact_ref, run_id) -> dict`
- Produces CLI action: `--action revision-template`
- Changes: `_creation_template_manifest()` no longer contains `revision_loop`

- [ ] **Step 1: Write failing template and revision-run tests**

```python
def test_creation_template_has_no_revision_loop(self):
    manifest = stage_dag._creation_template_manifest()
    stage_ids = [stage["stage_id"] for stage in manifest["stages"]]
    self.assertNotIn("revision_loop", stage_ids)
    persistence = next(
        stage for stage in manifest["stages"]
        if stage["stage_id"] == "persistence"
    )
    self.assertEqual(
        persistence["when"],
        {"stage_id": "confirmation_gate", "outcome": "approved"},
    )

def test_build_revision_manifest_increments_iteration_and_preserves_root(self):
    parent = stage_dag._creation_template_manifest()
    parent["status"] = "completed"
    parent["outcome"] = "revision_requested"
    revision = stage_dag.build_revision_manifest(
        parent,
        feedback=[{"page": 5, "issue": "钩子偏弱", "instruction": "加强悬念"}],
        artifact_ref="picturebook/script_v1.md",
        run_id="20260922-example-0002",
    )
    self.assertEqual(revision["intent"], "revision")
    self.assertEqual(revision["iteration"], 2)
    self.assertEqual(revision["root_run_id"], parent["root_run_id"])
    self.assertEqual(revision["revision_of_run_id"], parent["run_id"])
```

- [ ] **Step 2: Run tests to verify failure**

```powershell
python .\plugins\picturebook-screenwriter\tests\test_stage_dag.py -v
```

Expected: FAIL because the revision builder and new template do not exist.

- [ ] **Step 3: Update the creation template**

Use v2 top-level fields and remove `revision_loop`. Add:

```python
{
    "stage_id": "persistence",
    "assignee": "pb-persistence-agent",
    "depends_on": ["confirmation_gate"],
    "status": "pending",
    "gate": "none",
    "outcome": None,
    "when": {
        "stage_id": "confirmation_gate",
        "outcome": "approved",
    },
    "input_refs": [],
    "output_refs": [],
    "skip_reason": None,
    "blocked_reason": None,
}
```

Keep `knowledge_reminder` dependent on `persistence`.

- [ ] **Step 4: Add the revision template**

Create these stages in order:

```text
revision_init
knowledge_load
revision_delegate
preflight
collision_check
qa
qa_synthesis
confirmation_gate
persistence
knowledge_reminder
```

Dependencies:

```text
knowledge_load      -> revision_init
revision_delegate   -> revision_init, knowledge_load
preflight           -> revision_delegate
collision_check     -> revision_delegate
qa                  -> preflight, collision_check
qa_synthesis        -> qa
confirmation_gate   -> qa_synthesis
persistence         -> confirmation_gate, when approved
knowledge_reminder  -> persistence
```

- [ ] **Step 5: Implement build_revision_manifest**

```python
def build_revision_manifest(parent_run, feedback, artifact_ref, run_id):
    parent = validate_manifest(parent_run)
    if parent["status"] != "completed":
        raise StageDagError("父 run 必须 completed")
    if parent["outcome"] != "revision_requested":
        raise StageDagError("父 run outcome 必须为 revision_requested")
    if not isinstance(feedback, list) or not feedback:
        raise StageDagError("revision_feedback 必须非空")
    errors = []
    _require_nonempty_str(artifact_ref, "artifact_ref", errors)
    if errors:
        raise StageDagError(errors)

    manifest = _revision_template_manifest()
    manifest["run_id"] = run_id
    manifest["root_run_id"] = parent["root_run_id"]
    manifest["revision_of_run_id"] = parent["run_id"]
    manifest["iteration"] = parent["iteration"] + 1
    manifest["artifact_type"] = parent["artifact_type"]
    manifest["project_root"] = parent["project_root"]
    manifest["revision_feedback"] = copy.deepcopy(feedback)
    manifest["source_artifact_ref"] = artifact_ref
    return validate_manifest(manifest)
```

- [ ] **Step 6: Add the revision-template CLI action**

Add `"revision-template"` to CLI choices and print `_revision_template_manifest()` as JSON.

- [ ] **Step 7: Run focused tests**

```powershell
python .\plugins\picturebook-screenwriter\tests\test_stage_dag.py -v
```

Expected: PASS for creation-template, revision-template, and lineage tests.

- [ ] **Step 8: Commit**

```powershell
git add plugins/picturebook-screenwriter/scripts/stage_dag.py plugins/picturebook-screenwriter/tests/test_stage_dag.py
git commit -m "feat(dag): create revision runs with full revalidation"
```

---

### Task 4: Make Codex and Sequential Dispatch Condition-Aware

**Files:**
- Modify: `plugins/picturebook-screenwriter/scripts/stage_dag_codex.py`
- Test: `plugins/picturebook-screenwriter/tests/test_stage_dag_codex.py`

**Interfaces:**
- Consumes: `stage_dag.next_batches()`, `resolve_stage_decisions()`, `finalize_run()`
- Produces: dispatch plan schema `pb-dispatch-plan-v2`
- Produces: `lead_actions`, `decision_required`, `deferred_stages`, `follow_up_action`

- [ ] **Step 1: Write failing adapter tests**

Add a local helper:

```python
def _fast_forward_without_gate(self, manifest):
    for stage_id in (
        "session_init",
        "brief_gate",
        "knowledge_load",
        "creation_delegate",
        "preflight",
        "collision_check",
        "qa",
        "qa_synthesis",
    ):
        for status in ("ready", "running", "done"):
            manifest = stage_dag.transition_stage(
                manifest,
                stage_id,
                status,
            )
    return manifest

def _complete_confirmation(self, manifest, outcome):
    for status in ("ready", "running"):
        manifest = stage_dag.transition_stage(
            manifest,
            "confirmation_gate",
            status,
        )
    return stage_dag.transition_stage(
        manifest,
        "confirmation_gate",
        "done",
        outcome=outcome,
    )
```

Add tests:

```python
def test_confirmation_gate_is_lead_owned_and_waits_for_user(self):
    manifest = stage_dag._creation_template_manifest()
    manifest = self._fast_forward_without_gate(manifest)
    plan = stage_dag_codex.build_dispatch_plan(manifest)

    self.assertEqual(plan["status"], "waiting_for_user")
    self.assertEqual(
        plan["decision_required"]["stage_id"],
        "confirmation_gate",
    )
    self.assertEqual(plan["next_batches"], [])

def test_approved_confirmation_plans_persistence_only(self):
    manifest = stage_dag._creation_template_manifest()
    manifest = self._fast_forward_without_gate(manifest)
    manifest = self._complete_confirmation(manifest, "approved")
    plan = stage_dag_codex.build_dispatch_plan(manifest)

    self.assertEqual(
        [item["stage_id"] for item in plan["next_batches"][0]["stages"]],
        ["persistence"],
    )

def test_revision_request_returns_follow_up_action(self):
    manifest = stage_dag._creation_template_manifest()
    manifest = self._fast_forward_without_gate(manifest)
    manifest = self._complete_confirmation(manifest, "revision_requested")
    manifest = stage_dag.finalize_run(manifest, "revision_requested")
    plan = stage_dag_codex.build_dispatch_plan(manifest)

    self.assertEqual(plan["status"], "run_completed")
    self.assertEqual(plan["run_outcome"], "revision_requested")
    self.assertEqual(
        plan["follow_up_action"]["action"],
        "create_revision_run",
    )
```

- [ ] **Step 2: Run tests to verify failure**

```powershell
python .\plugins\picturebook-screenwriter\tests\test_stage_dag_codex.py -v
```

Expected: FAIL because the adapter still uses `parallel_batches()` and returns all future batches.

- [ ] **Step 3: Replace full-plan generation with current-batch generation**

`build_dispatch_plan` must:

1. Validate the manifest.
2. Return `run_completed` when run status is `completed` or `cancelled`.
3. Find ready lead-owned `confirmation_gate`.
4. Return `waiting_for_user` for that gate.
5. Otherwise place ready lead-owned stages in `lead_actions`.
6. Place ready subagent stages in `next_batches`.
7. Put conditional but inactive stages in `deferred_stages`.
8. Put blocked decisions in `blocked_reasons`.

Use:

```python
LEAD_OWNERS = {"picturebook-screenwriter-team-lead"}
```

- [ ] **Step 4: Enrich task envelope without changing its schema**

Add run context inside the existing `inputs` object:

```python
"inputs": {
    "input_refs": list(target["input_refs"]),
    "run_context": {
        "root_run_id": validated["root_run_id"],
        "revision_of_run_id": validated["revision_of_run_id"],
        "iteration": validated["iteration"],
        "revision_feedback": copy.deepcopy(
            validated["revision_feedback"]
        ),
        "source_artifact_ref": validated["source_artifact_ref"],
    },
}
```

Keep `schema_version` as `pb-stage-task-v1`.

- [ ] **Step 5: Make sequential fallback use the same decision engine**

`build_sequential_fallback` must return only current actionable stages:

```python
{
    "schema_version": "pb-dispatch-plan-v2",
    "status": "ready",
    "lead_actions": [...],
    "next_batches": [...],
    "decision_required": None,
    "mode": "sequential",
}
```

Do not expand persistence before confirmation approval.

- [ ] **Step 6: Run adapter tests**

```powershell
python .\plugins\picturebook-screenwriter\tests\test_stage_dag_codex.py -v
```

Expected: PASS for approval, revision request, and fallback tests.

- [ ] **Step 7: Commit**

```powershell
git add plugins/picturebook-screenwriter/scripts/stage_dag_codex.py plugins/picturebook-screenwriter/tests/test_stage_dag_codex.py
git commit -m "feat(dag): make codex dispatch condition-aware"
```

---

### Task 5: Update Skills, Contract, and User-Facing Workflow

**Files:**
- Modify: `plugins/picturebook-screenwriter/skills/stage-orchestration/SKILL.md`
- Modify: `plugins/picturebook-screenwriter/skills/picturebook-screenwriter/SKILL.md`
- Modify: `plugins/picturebook-screenwriter/config/plugin-contract.json`
- Test: `plugins/picturebook-screenwriter/tests/test_plugin_contract.py`
- Test: `plugins/picturebook-screenwriter/tests/test_skill_contract.py`

**Interfaces:**
- Consumes: v2 scheduler and dispatch plan
- Produces: documented lead-owned confirmation process
- Produces: contract fields for confirmation outcomes and revision runs

- [ ] **Step 1: Add failing contract tests**

Add assertions for:

```python
self.assertEqual(
    contract["stage_workflow"]["run_schema"],
    "pb-stage-run-v2",
)
self.assertEqual(
    contract["stage_workflow"]["confirmation_outcomes"],
    ["approved", "revision_requested", "cancelled"],
)
self.assertTrue(
    contract["stage_workflow"]["revision_reruns"]
    == ["preflight", "collision_check", "qa", "qa_synthesis"]
)
```

Add skill assertions:

```python
stage_skill = (
    ROOT / "skills" / "stage-orchestration" / "SKILL.md"
).read_text(encoding="utf-8")
entry_skill = ENTRY_SKILL.read_text(encoding="utf-8")

self.assertIn("waiting_for_user", stage_skill)
self.assertIn("revision_requested", stage_skill)
self.assertIn("不得派发子 Agent", stage_skill)
self.assertIn("重新执行 preflight、collision_check 和 qa", entry_skill)
```

- [ ] **Step 2: Run contract tests to verify failure**

```powershell
python .\plugins\picturebook-screenwriter\tests\test_plugin_contract.py -v
python .\plugins\picturebook-screenwriter\tests\test_skill_contract.py -v
```

Expected: FAIL because the new contract fields and wording are missing.

- [ ] **Step 3: Update plugin-contract.json**

Add:

```json
"stage_workflow": {
  "run_schema": "pb-stage-run-v2",
  "confirmation_outcomes": [
    "approved",
    "revision_requested",
    "cancelled"
  ],
  "revision_reruns": [
    "preflight",
    "collision_check",
    "qa",
    "qa_synthesis"
  ],
  "lead_owned_gates": [
    "confirmation_gate",
    "asset_confirmation",
    "asset_final_confirmation"
  ],
  "skipped_is_terminal": true
}
```

- [ ] **Step 4: Update stage-orchestration skill**

Document:

- `--action plan` returns current batches, not future unconditional batches.
- `waiting_for_user` meaning.
- Lead-owned gates.
- Approved and revision-requested flows.
- No `revision_loop` in creation templates.
- Revision run creation and full revalidation.
- Sequential fallback uses the same decision engine.

- [ ] **Step 5: Update the entry skill**

Document user-visible behavior:

- approved → persistence.
- revision requested → no persistence, create revision run.
- revision run reruns preflight, collision_check, qa, qa_synthesis.
- revision returns to confirmation.

- [ ] **Step 6: Run contract tests**

```powershell
python .\plugins\picturebook-screenwriter\tests\test_plugin_contract.py -v
python .\plugins\picturebook-screenwriter\tests\test_skill_contract.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add plugins/picturebook-screenwriter/skills/stage-orchestration/SKILL.md plugins/picturebook-screenwriter/skills/picturebook-screenwriter/SKILL.md plugins/picturebook-screenwriter/config/plugin-contract.json plugins/picturebook-screenwriter/tests/test_plugin_contract.py plugins/picturebook-screenwriter/tests/test_skill_contract.py
git commit -m "docs(dag): document conditional revision workflow"
```

---

### Task 6: Add End-to-End Coverage and Complete Regression Verification

**Files:**
- Modify: `plugins/picturebook-screenwriter/tests/test_stage_dag.py`
- Modify: `plugins/picturebook-screenwriter/tests/test_stage_dag_codex.py`
- Modify: `docs/CHANGELOG.md`

**Interfaces:**
- Consumes: all v2 scheduler and adapter interfaces
- Produces: end-to-end approval, revision, repeated-revision, cancellation, and migration coverage

- [ ] **Step 1: Add end-to-end tests**

Add:

```python
@staticmethod
def _complete_confirmation(manifest, outcome):
    for status in ("ready", "running"):
        manifest = stage_dag.transition_stage(
            manifest,
            "confirmation_gate",
            status,
        )
    return stage_dag.transition_stage(
        manifest,
        "confirmation_gate",
        "done",
        outcome=outcome,
    )

def test_creation_revision_round_reruns_required_checks(self):
    creation = stage_dag._creation_template_manifest()
    creation = self._fast_forward(creation, [
        "session_init",
        "brief_gate",
        "knowledge_load",
        "creation_delegate",
        "preflight",
        "collision_check",
        "qa",
        "qa_synthesis",
    ])
    creation = self._complete_confirmation(
        creation,
        "revision_requested",
    )
    creation = stage_dag.finalize_run(creation, "revision_requested")

    revision = stage_dag.build_revision_manifest(
        creation,
        feedback=[{"page": 5, "issue": "钩子弱", "instruction": "加强"}],
        artifact_ref="picturebook/script_v1.md",
        run_id="20260922-example-0002",
    )
    stage_ids = [stage["stage_id"] for stage in revision["stages"]]

    self.assertIn("preflight", stage_ids)
    self.assertIn("collision_check", stage_ids)
    self.assertIn("qa", stage_ids)
    self.assertIn("qa_synthesis", stage_ids)
    self.assertEqual(revision["iteration"], 2)

def test_repeated_revision_preserves_distinct_run_ids(self):
    parent = stage_dag._creation_template_manifest()
    parent = self._fast_forward(parent, [
        "session_init",
        "brief_gate",
        "knowledge_load",
        "creation_delegate",
        "preflight",
        "collision_check",
        "qa",
        "qa_synthesis",
    ])
    parent = self._complete_confirmation(
        parent,
        "revision_requested",
    )
    parent = stage_dag.finalize_run(parent, "revision_requested")
    first = stage_dag.build_revision_manifest(
        parent,
        feedback=[{"page": 1, "issue": "问题", "instruction": "修改"}],
        artifact_ref="picturebook/script_v1.md",
        run_id="20260922-example-0002",
    )
    first["status"] = "completed"
    first["outcome"] = "revision_requested"
    second = stage_dag.build_revision_manifest(
        first,
        feedback=[{"page": 2, "issue": "问题", "instruction": "再修改"}],
        artifact_ref="picturebook/script_v2.md",
        run_id="20260922-example-0003",
    )
    self.assertNotEqual(first["run_id"], second["run_id"])
    self.assertEqual(second["iteration"], 3)
```

Add an adapter end-to-end test proving:

```python
self.assertNotIn(
    "persistence",
    [
        stage["stage_id"]
        for batch in revision_plan["next_batches"]
        for stage in batch["stages"]
    ],
)
```

before the revision confirmation is approved.

- [ ] **Step 2: Run focused end-to-end tests to verify failure**

```powershell
python .\plugins\picturebook-screenwriter\tests\test_stage_dag.py -v
python .\plugins\picturebook-screenwriter\tests\test_stage_dag_codex.py -v
```

Expected: FAIL until the final integration details are complete.

- [ ] **Step 3: Fix integration gaps only**

Limit production edits to interface mismatches found by the end-to-end tests. Do not add unrelated workflow features.

- [ ] **Step 4: Update CHANGELOG**

Add under the appropriate release section:

```markdown
- Stage workflows now represent approval and revision as exclusive outcomes.
- Revision rounds create new runs and rerun preflight, collision, and QA checks.
- v1 run manifests require explicit migration.
```

- [ ] **Step 5: Run the complete plugin suite**

```powershell
$files = git ls-files 'plugins/picturebook-screenwriter/**/test_*.py'
$failures = @()
foreach ($file in $files) {
  $output = python $file 2>&1
  if ($LASTEXITCODE -ne 0) {
    $failures += [PSCustomObject]@{ File = $file; Output = ($output -join "`n") }
  }
}
if ($failures.Count -gt 0) {
  $failures | Format-List
  exit 1
}
```

Expected: all test files pass.

- [ ] **Step 6: Run release gates**

```powershell
python .\scripts\governance_check.py
python .\scripts\package_check.py
python C:\Users\lvan\.codex\skills\.system\plugin-creator\scripts\validate_plugin.py .\plugins\picturebook-screenwriter
node --test .\plugins\picturebook-screenwriter\skills\image-generate\generate.test.js
node .\plugins\picturebook-screenwriter\skills\staging-planner\scripts\run_regression.js
```

Expected: all commands exit 0.

- [ ] **Step 7: Commit**

```powershell
git add docs/CHANGELOG.md plugins/picturebook-screenwriter/tests/test_stage_dag.py plugins/picturebook-screenwriter/tests/test_stage_dag_codex.py plugins/picturebook-screenwriter/scripts/stage_dag.py plugins/picturebook-screenwriter/scripts/stage_dag_codex.py
git commit -m "test(dag): cover conditional revision workflow end to end"
```

---

## Final Verification Checklist

- [ ] `pb-stage-run-v2` is the only executable schema.
- [ ] v1 migration is explicit and ambiguity-safe.
- [ ] confirmation gate outcomes are exactly three values.
- [ ] persistence never becomes ready without approved.
- [ ] revision_requested skips persistence and knowledge_reminder.
- [ ] revision runs rerun preflight, collision_check, qa, and qa_synthesis.
- [ ] every revision round stops at a new confirmation gate.
- [ ] skipped is terminal and does not halt unrelated branches.
- [ ] Codex plan and sequential fallback use `next_batches`.
- [ ] lead-owned gates are never dispatched to subagents.
- [ ] all plugin tests pass.
- [ ] governance, package, plugin validation, and Node regression gates pass.
