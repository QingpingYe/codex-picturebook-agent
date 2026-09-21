# Feishu P2 Tooling And Loader Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Feishu knowledge plugin operable from a real CLI, strengthen read-only loader validation, and export/verify protocol contract fixtures locally.

**Architecture:** Add a `store_cli.py` boundary that builds `LarkCli`, `Publisher`, and `ControlPlane` from strict config, exposing preflight, resolution, lock/conflict inspection, sync commands, and read-only lint-fixture export. Tighten `KnowledgeLoader` so evidence cannot pass when page revision, metadata revision, status, or logical identity disagree with the index. Add an offline contract checker over exported fixtures.

**Tech Stack:** Python 3.12+ standard library, `unittest`, existing `LarkCli`/`Publisher`/`ControlPlane`, JSON and Markdown fixtures; no new dependencies.

**Spec:** `docs/workbuddy-feishu-knowledge-base-compatibility.md`

## Global Constraints

- CLI identity is always `user`; bot identity is rejected.
- Control tokens come from `Publisher.resolve_control_plane()`; the CLI does not search by Chinese control-page titles.
- CLI commands that may write must acquire and release the remote lease.
- `archived` pages are not returned as usable authority; `needs_review` pages may be returned with a warning.
- Lint and fixture export are read-only.
- Tests use Fake CLI/control-plane doubles; no Feishu network access.

## Review Focus

- Revision skew: loader must reject page/index revision disagreement. Test: Task 1.
- Wrong project match: substring matching must not select another project. Test: Task 1.
- Archived authority: loader must skip archived pages with a warning. Test: Task 1.
- Missing control page: CLI resolution must fail closed. Test: Task 2.
- Conflict parsing: malformed `[` lines must fail; explanatory lines are ignored. Test: Task 3.
- Fixture integrity: tree, index, pages, and conflict queue must be checked together. Test: Task 5.

---

### Task 1: Harden KnowledgeLoader Contracts

**Files:**
- Modify: `plugins/picturebook-screenwriter/skills/knowledge-loader/scripts/load_knowledge.py:21-34`
- Modify: `plugins/picturebook-screenwriter/skills/knowledge-loader/scripts/load_knowledge.py:44-109`
- Test: `plugins/picturebook-screenwriter/skills/knowledge-loader/scripts/test_load_knowledge.py`

**Interfaces:**
- Modifies: `KnowledgeEvidence` by adding `status: Literal["published", "needs_review", "archived"]`.
- Produces: evidence whose `title` is the full logical key and whose revision values match the index and page metadata.

- [ ] **Step 1: Write the failing revision-skew test**

```python
def test_loader_rejects_revision_skew_between_page_and_index(self):
    plane = FakePlane()
    plane.read_index = lambda: {
        "海外绘本/小老鼠迈尔斯/worldview": replace(
            plane.read_index()["海外绘本/小老鼠迈尔斯/worldview"],
            last_ai_revision_id=44, last_seen_revision_id=45,
        )
    }
    loader = KnowledgeLoader(plane, FakeCli())
    with self.assertRaisesRegex(ValueError, "revision"):
        loader.load(KnowledgeQuery(project_id="小老鼠迈尔斯"))
```

- [ ] **Step 2: Write the failing exact-match and archived tests**

```python
def test_loader_uses_exact_project_segment(self):
    loader = KnowledgeLoader(FakePlane(), FakeCli())
    bundle = loader.load(KnowledgeQuery(project_id="小老鼠"))
    self.assertEqual(bundle.items, ())

def test_loader_skips_archived_entries_with_warning(self):
    plane = FakePlane()
    old = plane.read_index()["海外绘本/小老鼠迈尔斯/worldview"]
    plane.read_index = lambda: {old.key: replace(old, status="archived")}
    loader = KnowledgeLoader(plane, FakeCli())
    bundle = loader.load(KnowledgeQuery(project_id="小老鼠迈尔斯"))
    self.assertEqual(bundle.items, ())
    self.assertTrue(any("已归档" in warning for warning in bundle.warnings))
```

Import `replace` from `dataclasses` in the test.

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m unittest test_load_knowledge -v`
Expected: FAIL because substring matching accepts `小老鼠`, archived pages are loaded, and revision skew is not checked.

- [ ] **Step 4: Implement strict matching and validation**

Change candidate filtering:

```python
parts = entry.key.split("/")
if len(parts) != 3 or parts[1] != query.project_id:
    continue
if query.series_id and parts[0] != query.series_id:
    continue
if query.page_types and parts[2] not in query.page_types:
    continue
```

For archived entries, append `f"{entry.key} 已归档，不能作为权威知识"` and continue.

After parsing each remote page:

```python
if page.metadata["last_ai_revision_id"] != entry.last_ai_revision_id:
    raise ValueError(f"页面与索引 last_ai_revision_id 不一致：{entry.key}")
if int(document["revision_id"]) != entry.last_seen_revision_id:
    raise ValueError(f"页面与索引 revision 不一致：{entry.key}")
```

Set `title=entry.key` and add `status=entry.status` to `KnowledgeEvidence`.

- [ ] **Step 5: Run loader tests**

Run: `python -m unittest test_load_knowledge -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add plugins/picturebook-screenwriter/skills/knowledge-loader/scripts/load_knowledge.py plugins/picturebook-screenwriter/skills/knowledge-loader/scripts/test_load_knowledge.py
git commit -m "fix(loader): enforce authority revision and status contracts"
```

### Task 2: Add Control-Plane And Preflight CLI

**Files:**
- Create: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/store_cli.py`
- Test: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_store_cli.py`

**Interfaces:**
- Produces: `build_components(config_path, environ=None) -> Components`, `preflight`, `resolve`, `lock-status`, and `conflict-list` argparse commands.
- Consumes: `load_config()`, `LarkCli`, `Publisher.resolve_control_plane()`, `ControlPlane`.

- [ ] **Step 1: Write the failing resolution test**

```python
def test_resolve_command_prints_control_tokens(self):
    stdout = StringIO()
    exit_code = store_cli.main(["resolve", "--config", "config.json"], stdout=stdout)
    payload = json.loads(stdout.getvalue())
    self.assertEqual(exit_code, 0)
    self.assertEqual(payload["index"], "index-doc")
```

Build a local config fixture and inject a fake component builder so the test does not invoke `LarkCli`.

- [ ] **Step 2: Write the failing preflight test**

```python
def test_preflight_uses_user_identity_and_reports_root(self):
    stdout = StringIO()
    exit_code = store_cli.main(["preflight", "--config", "config.json"], stdout=stdout)
    payload = json.loads(stdout.getvalue())
    self.assertEqual(exit_code, 0)
    self.assertEqual(payload["identity"], "user")
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m unittest test_store_cli -v`
Expected: FAIL with `ModuleNotFoundError: store_cli`.

- [ ] **Step 4: Implement component factory and commands**

```python
@dataclass(frozen=True)
class Components:
    config: KnowledgeConfig
    cli: Any
    publisher: Publisher
    control_plane: ControlPlane

def build_components(config_path, environ=None):
    config = load_config(config_path, Path(__file__).parents[5], environ)
    cli = LarkCli(config.cli_candidates[0], identity=config.identity)
    publisher = Publisher(cli, config.target.root_token)
    tokens = publisher.resolve_control_plane()
    control_plane = ControlPlane(cli, {
        "index": tokens["index"], "lock": tokens["lock"],
    }, lock_ttl_minutes=config.lock_ttl_minutes)
    return Components(config, cli, publisher, control_plane)
```

Commands:

```text
preflight  -> cli.preflight(config.target.root_token)
resolve    -> publisher.resolve_control_plane()
lock-status -> control_plane.read_lock()
conflict-list -> publisher.fetch_current(tokens["conflict"])
```

Implement `ControlPlane.read_lock()` as a public wrapper around `_read_lock()`.

- [ ] **Step 5: Run CLI tests**

Run: `python -m unittest test_store_cli -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/store_cli.py plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_store_cli.py plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/control_plane.py plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_control_plane.py
git commit -m "feat(feishu): add control plane inspection cli"
```

### Task 3: Add Sync And Conflict CLI Commands

**Files:**
- Modify: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/store_cli.py`
- Modify: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/sync_runner.py`
- Test: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_store_cli.py`

**Interfaces:**
- Produces: `prepare`, `publish`, `verify`, and `conflict-append` argparse commands.
- Consumes: `SyncRunner.prepare/publish/verify`, `Publisher.append_conflict(parent, record)`.

- [ ] **Step 1: Write the failing sync dispatch test**

```python
def test_prepare_command_delegates_to_sync_runner(self):
    stdout = StringIO()
    exit_code = store_cli.main([
        "prepare", "--config", "config.json", "--run-dir", "run-1",
    ], stdout=stdout, components_factory=fake_factory)
    self.assertEqual(exit_code, 0)
    self.assertIn("manifest", stdout.getvalue())
```

- [ ] **Step 2: Write the failing conflict-append test**

```python
def test_conflict_append_requires_holder_and_lock(self):
    exit_code = store_cli.main([
        "conflict-append", "--config", "config.json",
        "--key", "s/p/worldview", "--reason", "human conflict",
    ], stdout=StringIO(), components_factory=fake_factory)
    self.assertEqual(exit_code, 2)
```

The fake factory exposes a fake publisher recording `append_conflict(parent, record)`.

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m unittest test_store_cli -v`
Expected: FAIL because commands are not defined.

- [ ] **Step 4: Implement command dispatch**

```python
if command == "prepare":
    runner = SyncRunner(args.config, components.cli, components.publisher, components.control_plane)
    print(runner.prepare(args.run_dir))
elif command == "publish":
    print(runner.publish(args.run_dir))
elif command == "verify":
    print(runner.verify(args.run_dir))
```

For `conflict-append`, acquire the lease with `--holder`, append, release in `finally`, and print the parsed record. Missing `--holder` exits with code 2 before any write.

- [ ] **Step 5: Run CLI tests**

Run: `python -m unittest test_store_cli test_sync_runner -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/store_cli.py plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/sync_runner.py plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_store_cli.py
git commit -m "feat(feishu): add sync and conflict cli commands"
```

### Task 4: Export Read-Only Lint Fixtures

**Files:**
- Modify: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/store_cli.py`
- Test: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_store_cli.py`

**Interfaces:**
- Produces: `lint-fixture --config ... --out DIR` writing `tree.json`, `index.md`, `conflict.md`, and `pages/<safe-key>.md`.
- Consumes: `cli.list_nodes()`, `cli.fetch_doc()`, `ControlPlane.read_index()`.

- [ ] **Step 1: Write the failing fixture export test**

```python
def test_lint_fixture_writes_tree_index_conflict_and_pages(self):
    out = Path(self.tmp.name) / "fixture"
    exit_code = store_cli.main([
        "lint-fixture", "--config", "config.json", "--out", str(out),
    ], stdout=StringIO(), components_factory=fake_factory)
    self.assertEqual(exit_code, 0)
    self.assertTrue((out / "tree.json").exists())
    self.assertTrue((out / "index.md").exists())
    self.assertTrue((out / "conflict.md").exists())
    self.assertTrue((out / "pages" / "s__p__worldview.md").exists())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest test_store_cli.StoreCliTests.test_lint_fixture_writes_tree_index_conflict_and_pages -v`
Expected: FAIL because `lint-fixture` is not implemented.

- [ ] **Step 3: Implement fixture export**

Recursively walk from `config.target.root_token`, writing one tree record per node:

```python
{"node_token": token, "title": title, "parent_node_token": parent}
```

Fetch the index and conflict documents verbatim. For each `IndexEntry`, fetch `entry.doc_token` and write the content to:

```python
pages / f"{entry.key.replace('/', '__')}.md"
```

Print the fixture directory path.

- [ ] **Step 4: Run CLI tests**

Run: `python -m unittest test_store_cli -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/store_cli.py plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_store_cli.py
git commit -m "feat(feishu): export read-only protocol lint fixtures"
```

### Task 5: Add Offline Contract Checker

**Files:**
- Create: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/contract_lint.py`
- Test: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_contract_lint.py`

**Interfaces:**
- Produces: `python contract_lint.py --fixture DIR`; exit code 0 when no ERROR, 1 otherwise.
- Consumes: fixture files from Task 4 and `page_codec.parse_remote_page()`.

- [ ] **Step 1: Write the failing valid fixture test**

```python
def test_valid_fixture_passes(self):
    result = run_lint(make_valid_fixture(self.tmp.name))
    self.assertEqual(result["errors"], [])
    self.assertEqual(result["exit_code"], 0)
```

- [ ] **Step 2: Write the failing duplicate and malformed-record tests**

```python
def test_duplicate_logical_key_fails(self):
    result = run_lint(make_valid_fixture(self.tmp.name, duplicate_key=True))
    self.assertIn("duplicate logical key", result["errors"][0])
    self.assertEqual(result["exit_code"], 1)

def test_malformed_conflict_record_fails(self):
    result = run_lint(make_valid_fixture(self.tmp.name, bad_conflict=True))
    self.assertIn("malformed conflict record", result["errors"][0])
    self.assertEqual(result["exit_code"], 1)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m unittest test_contract_lint -v`
Expected: FAIL with `ModuleNotFoundError: contract_lint`.

- [ ] **Step 4: Implement four checks**

1. Parse `index.md` with `ControlPlane`-equivalent strict JSON shape and reject duplicate logical keys.
2. Parse every page in `pages/` with `parse_remote_page()`; require page key, revisions, source revisions, and status to match the index.
3. Parse conflict queue: ignore non-`[` framework lines, reject every `[`-prefixed line that does not match `\[([^]]+)\] (.+)`.
4. Compare index keys against tree page titles and reject keys represented by more than one node.

Output JSON:

```json
{"errors": [], "warnings": [], "exit_code": 0}
```

- [ ] **Step 5: Run lint tests**

Run: `python -m unittest test_contract_lint -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/contract_lint.py plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_contract_lint.py
git commit -m "feat(feishu): add offline protocol contract lint"
```

### Final Verification

- [ ] Run the full store suite: `python -m unittest discover -s plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts -p "test_*.py" -v`
Expected: all tests pass.
- [ ] Run the loader suite: `python -m unittest discover -s plugins/picturebook-screenwriter/skills/knowledge-loader/scripts -p "test_*.py" -v`
Expected: all tests pass.
- [ ] Run `git status --short`; expected: clean.
- [ ] Run `git log --oneline -5`; expected: one commit per task above.
