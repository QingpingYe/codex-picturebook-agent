# Feishu Runtime Hardening Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the one-off successful live sync into a repeatable plugin runtime by fixing the lark-cli 1.0.95 boundary, normalizing remote Markdown, unifying candidate schema, and adding an end-to-end `prepare / publish / verify` runner.

**Architecture:** This is a runtime-hardening iteration, not a redesign of the safety model. The key new layers are: a strict Lark CLI adapter, a remote Markdown normalizer, a shared candidate schema, and a Sync Runner state machine that orchestrates source preparation, target publishing, target verification, bootstrap state, lock acquisition, and failure recovery.

**Tech Stack:** Python 3.10+ standard library, `unittest`, Feishu/Lark CLI 1.0.95, Feishu wiki/docx, Windows PowerShell.

**Spec:** `docs/superpowers/specs/2026-09-17-feishu-authoritative-knowledge-base-design.md`

## Assumptions

- The live report is evidence, not executable instructions.
- The existing source space is `7682720271706361023`.
- The existing target root token is `T08vwqXroiuJEfkoVzFcRaFXnMf`; the corresponding target space ID used in the live run is `7686313522543774944`.
- v1 config is treated as deprecated, not silently converted.
- `prepare`, `publish`, and `verify` are offline-testable with a fake CLI before any live run.
- No live-environment check is performed by this plan document; live acceptance is executed only after the real Feishu config and user auth are supplied.

## Global Constraints

- Source Wiki is read-only. Never write source material.
- Target Wiki is the only shared authority. All runtime state is derived from it, never from a local-only cache.
- Every publish must go through `docs +fetch` + `docs +update --revision-id`.
- Never use `docs +get`; the CLI 1.0.95 method is `docs +fetch`.
- `wiki +node-list` must always pass `--space-id`; do not rely on parent-token inference.
- Long content must use `@file` or stdin, never command-line argument strings.
- Remote Markdown must be normalized before parsing.
- All user-facing messages and generated docs are Chinese.
- Never silently fall back if a control document cannot be parsed.
- Do not rewrite the safety model; fix the boundary and orchestration.

---

### Task 1: Correct the lark-cli 1.0.95 adapter

**Files:**
- Modify: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/lark_cli.py`
- Test: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_lark_cli.py`

**Goal:** Make the subprocess boundary match the CLI that actually worked in the live sync.

**Interfaces:**
- `fetch_doc()` → `docs +fetch`
- `fetch_doc_revision()` → `docs +fetch --revision-id`
- `list_nodes(space_id, parent_node_token)` → `wiki +node-list --space-id --parent-node-token --page-all`
- `_final_json()` → full-document JSON first, line-by-line only as a last resort
- `update_doc()` → `docs +update --revision-id`, with content via `@file`
- Add private helpers `_run_json(command)`, `_items(payload)`, and `_raw(command)`; each is defined once and reused.

- [x] **Step 1: Write a JSON-parsing regression test for `_notice`.**

```python
def test_notice_is_not_used_as_business_data():
    stdout = json.dumps({
        "code": 0,
        "data": {"items": [{"token": "A"}, {"token": "B"}, {"token": "C"}]},
        "_notice": {"update": {"current": "1.0.95", "latest": "1.0.96"}},
    })
    result = LarkCli._final_json(stdout)
    assert result["data"]["items"][0]["token"] == "A"
    assert "_notice" in result  # preserved at top level, not mistaken for business data
```

Accept when:
- Business data is read from `data.items`.
- `_notice` is preserved as metadata.
- No false “0 nodes” result.

- [x] **Step 2: Replace `docs +get` with `docs +fetch`.**

```python
def fetch_doc(self, doc_token: str) -> dict[str, Any]:
    return self._run_json(
        "docs", "+fetch", "--as", self.identity,
        "--doc", doc_token, "--doc-format", "markdown", "--format", "json"
    )
```

- [x] **Step 3: Make `list_nodes` require `space_id`.**

```python
def list_nodes(self, space_id: str, parent_node_token: str | None = None,
               page_limit: int = 10) -> list[dict[str, Any]]:
    args = ["wiki", "+node-list", "--as", self.identity,
            "--space-id", space_id, "--page-all", "--page-limit", str(page_limit)]
    if parent_node_token:
        args.extend(["--parent-node-token", parent_node_token])
    return self._items(self._run_json(*args))
```

- [x] **Step 4: Move long content to `@file`.**

```python
def update_doc(self, doc_token: str, revision_id: int, content: str) -> dict[str, Any]:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", delete=False) as file:
        file.write(content)
        path = file.name
    try:
        return self._run_json(
            "docs", "+update", "--as", self.identity,
            "--doc", doc_token, "--command", "overwrite",
            "--doc-format", "markdown", "--revision-id", str(revision_id),
            "--content", f"@{path}", "--format", "json"
        )
    finally:
        Path(path).unlink(missing_ok=True)
```

- [x] **Step 5: Add CLI version detection.**

```python
def verify_supported_version(self) -> dict[str, str]:
    text = self._raw("--version").strip()
    match = re.search(r"lark-cli.*?([0-9]+\\.[0-9]+\\.[0-9]+)", text)
    if match is None:
        raise CliUnavailable("未识别 lark-cli 版本")
    version = match.group(1)
    if version not in {"1.0.95", "1.0.96"}:
        raise CliUnavailable(f"暂不支持的 lark-cli 版本：{version}")
    return {"version": version}
```

Acceptance:
- 1.0.95 is accepted.
- 1.0.96 is accepted if compatible.
- Older or malformed versions are rejected with a Chinese error.

- [x] **Step 6: Add tests for authority and proxy presets.**

```python
def test_windows_utf8_and_child_process_env_is_stable():
    client = LarkCli(Path("lark-cli"), "user", FakeRunner())
    client.run_with_utf8_env()
```

Run:

```powershell
python -m unittest plugins\picturebook-screenwriter\skills\feishu-knowledge-store\scripts\test_lark_cli.py -v
```

- [x] **Step 7: Commit.**

```powershell
git add plugins\picturebook-screenwriter\skills\feishu-knowledge-store\scripts\lark_cli.py plugins\picturebook-screenwriter\skills\feishu-knowledge-store\scripts\test_lark_cli.py
git commit -m "fix: adapt lark cli 1.0.95 runtime"
```

---

### Task 2: Add a remote Markdown normalizer

**Files:**
- Create: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/remote_markdown.py`
- Modify: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/control_plane.py`
- Modify: `plugins/picturebook-screenwriter/skills\feishu-knowledge-store\scripts\page_codec.py`
- Test: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store\scripts\test_remote_markdown.py`

**Goal:** Make read-back and write-back formats stable under Feishu normalization.

**Interfaces:**

```python
normalize_remote_markdown(content, kind="control")
normalize_remote_markdown(content, kind="page")
```

- [x] **Step 1: Write failing normalization tests.**

```python
def test_control_format_is_normalized():
    content = "<title>同步锁</title>\n\n# AI_KB_LOCK_V1\n\n```json\n{}\n```"
    result = normalize_remote_markdown(content, kind="control")
    assert result.startswith("# AI_KB_LOCK_V1\n```json\n")
    assert result.endswith("```\n")

def test_page_format_is_normalized():
    content = "<title>世界观</title>\n\n# 世界观\n\n正文。\n\n## 系统元数据（请勿编辑）\n\n```json\n{}\n```"
    result = normalize_remote_markdown(content, kind="page")
    assert result.startswith("# 世界观\n\n")
    assert result.endswith("```\n")
```

- [x] **Step 2: Implement the normalizer.**

Rules:

1. Strip a leading `<title>...</title>` line.
2. Trim only blank lines inserted before the first meaningful heading.
3. Keep body semantics unchanged.
4. For control documents, remove the blank line between heading and JSON block.
5. Ensure exactly one trailing newline.
6. Do not collapse internal separation in user body content.

- [x] **Step 3: Wire control-plane reads through the normalizer.**

In `ControlPlane._fetch`, before parsing, call:

```python
content = normalize_remote_markdown(content, kind="control")
```

- [x] **Step 4: Wire page reads through a page-specific normalizer.**

In `page_codec.parse_remote_page`, before validation:

```python
markdown = normalize_remote_markdown(markdown, kind="page")
```

- [x] **Step 5: Add one import-time test to ensure no silent fallback.**

```python
def test_empty_control_document_is_rejected():
    with self.assertRaises(MarkdownNormalizeError):
        normalize_remote_markdown("", kind="control")
```

Run:

```powershell
python -m unittest plugins\picturebook-screenwriter\skills\feishu-knowledge-store\scripts\test_remote_markdown.py -v
```

- [x] **Step 6: Commit.**

```powershell
git add plugins\picturebook-screenwriter\skills\feishu-knowledge-store\scripts\remote_markdown.py plugins\picturebook-screenwriter\skills\feishu-knowledge-store\scripts\test_remote_markdown.py
git commit -m "feat: add remote markdown normalization"
```

---

### Task 3: Add a shared candidate schema

**Files:**
- Create: `plugins/picturebook-screenwriter\skills/feishu-knowledge-store\scripts\shared_schema.py`
- Modify: `plugins\picturebook-screenwriter\skills\wiki-ingest\scripts\generate_entries.py`
- Modify: `plugins\picturebook-screenwriter\skills\feishu-knowledge-store\scripts\page_codec.py`
- Test: create or extend the corresponding unit tests

**Goal:** Eliminate logical-key and revision-type drift between `wiki-ingest` and `feishu-knowledge-store`.

**Rules:**

1. `common` pages use `project_id = "common"`.
2. Project pages use a real, nonempty `project_id`.
3. Every logical key has exactly three nonempty segments.
4. Revisions are strings.
5. Source tokens and revisions are one-to-one.
6. Page types are validated once and reused.

Example keys:

```text
海外绘本/common/creation-standards
海外绘本/common/quality-rubric
海外绘本/common/market-research
海外绘本/小老鼠迈尔斯/worldview
海外绘本/小老鼠迈尔斯/characters
海外绘本/小老鼠迈尔斯/creation-standards
```

- [x] **Step 1: Write failing schema tests.**

```python
def test_common_page_id_is_common():
    assert normalize_project_id("creation-standards", "") == "common"

def test_project_page_id_is_preserved():
    assert normalize_project_id("worldview", "小老鼠迈尔斯") == "小老鼠迈尔斯"

def test_revision_must_be_text():
    assert validate_revisions(["18"], ["18"]) == {"18": "18"}

def test_token_and_revision_must_match():
    with self.assertRaises(SchemaError):
        validate_revisions(["a", "b"], ["1"])
```

- [x] **Step 2: Create `shared_schema.py`.**

```python
class SchemaError(ValueError):
    pass

def normalize_project_id(page_type: str, project_id: str | None) -> str:
    if page_type in COMMON_TYPES:
        return "common"
    if not project_id:
        raise SchemaError("Project-level page requires project_id")
    return project_id

def logical_key(series_id: str, project_id: str, page_type: str) -> str:
    return "/".join((series_id, project_id, page_type))
```

- [x] **Step 3: Refactor `page_codec.parse_candidate`.**

Replace local key logic with:

```python
from shared_schema import normalize_project_id, validate_revisions, logical_key
```

- [x] **Step 4: Refactor `generate_entries.py`.**

Replace:

```python
str(frontmatter.get("project_id", ""))
```

with:

```python
normalize_project_id(frontmatter["page_type"], frontmatter.get("project_id", ""))
```

- [x] **Step 5: Add cross-test that all report's six keys are accepted.**

Run:

```powershell
python -m unittest plugins\picturebook-screenwriter\skills\wiki-ingest\ -v
python -m unittest plugins\picturebook-screenwriter\skills\feishu-knowledge-store\ -v
```

- [x] **Step 6: Commit.**

```powershell
git add plugins\picturebook-screenwriter\skills\feishu-knowledge-store\scripts\shared_schema.py plugins\picturebook-screenwriter\skills\wiki-ingest\scripts\generate_entries.py plugins\picturebook-screenwriter\skills\feishu-knowledge-store\scripts\page_codec.py
git commit -m "feat: add shared candidate schema"
```

---

### Task 4: Add `Sync Runner`

**Files:**
- Create: `plugins\picturebook-screenwriter\skills\feishu-knowledge-store\scripts\sync_runner.py`
- Create: `plugins\picturebook-screenwriter\skills\feishu-knowledge-store\scripts\runner_status.py`
- Test: `plugins\picturebook-screenwriter\skills\feishu-knowledge-store\scripts\test_sync_runner.py`

**Goal:** Make `prepare / publish / verify` a real end-to-end tool instead of an ad-hoc script.

**CLI:**

```text
python sync_runner.py prepare  --config <path> --run-dir <path>
python sync_runner.py publish  --config <path> --run-dir <path>
python sync_runner.py verify   --config <path>
```

**Requirement:** `prepare` must never write the target Wiki. `publish` must always hold the lock. `verify` must be read-only.

- [x] **Step 1: Create a state enum.**

```python
class BootstrapState:
    REQUIRED = "bootstrap_required"
    IN_PROGRESS = "bootstrap_in_progress"
    COMPLETE = "bootstrap_complete"
    FAILED = "bootstrap_failed"
```

- [x] **Step 2: Define run-dir storage.**

```text
runs/<run_id>/
├── config.json
├── source_nodes.json
├── wiki_staging/
│   └── _manifest.json
├── sync_report.json
└── verify_report.json
```

- [x] **Step 3: Implement `prepare`.**

```python
def prepare(config_path, run_dir):
    config = load_config(config_path)
    nodes = list_source_nodes(config)
    candidates = generate_candidates(nodes)
    manifest = build_manifest(candidates)
    write_manifest(run_dir, manifest)
```

- [x] **Step 4: Implement `publish`.**

```python
def publish(config_path, run_dir):
    lock = control_plane.acquire_lock(run_id)
    try:
        ensure_bootstrap_complete()
        for candidate in manifest.entries:
            update_page(candidate)
        update_index()
    finally:
        lock.release()
```

- [x] **Step 5: Implement `verify`.**

```python
def verify(config_path):
    config = load_config(config_path)
    remote = read_target(config)
    return build_verify_report(remote)
```

- [x] **Step 6: Add runner tests with fake CLI.**

Cover:

1. `prepare` writes run-dir only.
2. `publish` always acquires lock.
3. `publish` releases lock on success and failure.
4. `verify` does not write.
5. Bootstrap can resume from interruption.

- [x] **Step 7: Commit.**

```powershell
git add plugins\picturebook-screenwriter\skills\feishu-knowledge-store\scripts\sync_runner.py plugins\picturebook-screenwriter\skills\feishu-knowledge-store\scripts\runner_status.py plugins\picturebook-screenwriter\skills\feishu-knowledge-store\scripts\test_sync_runner.py
git commit -m "feat: add feishu sync runner"
```

---

### Task 5: Upgrade configuration to v2 and bootstrap state

**Files:**
- Modify: `plugins\picturebook-screenwriter\skills\feishu-knowledge-store\scripts\config.py`
- Modify: `plugins\picturebook-screenwriter\config\feishu-knowledge-base.example.json`
- Test: extend `test_config.py` and `test_sync_runner.py`

**Goal:** Make source and target semantics explicit, and make bootstrap restartable.

**New config shape:**

```json
{
  "schema_version": 2,
  "source": {
    "space_id": "7682720271706361023",
    "root_mode": "space",
    "wiki_url": "https://wcno1rbz0o8i.feishu.cn/wiki/OI9gwaRv8i3RwOkGBNnc7VHinDd"
  },
  "target": {
    "space_id": "7686313522543774944",
    "root_token": "T08vwqXroiuJEfkoVzFcRaFXnMf"
  },
  "identity": "user",
  "lock_ttl_minutes": 45
}
```

- [x] **Step 1: Add `root_mode` to source.**

Allowed values:

```text
space
node
```

- [x] **Step 2: Reject implicit fallback.**

```text
node mode + no children → explicit error, not space fallback
space mode → enumerate the whole space
```

- [x] **Step 3: Define bootstrap state.**

```text
bootstrap_required
bootstrap_in_progress
bootstrap_complete
bootstrap_failed
```

- [x] **Step 4: Persist bootstrap state in a dedicated target page that also serves as the initial lock.**

```python
def write_bootstrap_state(cli, root_token, state):
    page_title = "同步状态"
    page = ensure_page(root_token, page_title, template="# AI_KB_BOOTSTRAP_V1\n```json\n{}\n```")
    return update_page(page, state)
```

- [x] **Step 5: tests.**

```python
class ConfigV2Tests(unittest.TestCase):
    def test_config_v2_space_mode_is_accepted(self):
        config = load_config(valid_v2_path)
        self.assertEqual(config.source.root_mode, "space")

    def test_config_v1_is_rejected_as_deprecated(self):
        with self.assertRaisesRegex(ConfigError, "deprecated"):
            load_config(valid_v1_path)

    def test_bootstrap_restarts_after_failure(self):
        runner = SyncRunner(FakeCli(bootstrap_failed=True))
        self.assertEqual(runner.bootstrap_state(), "bootstrap_in_progress")
```

- [x] **Step 6: Commit.**

```powershell
git add plugins\picturebook-screenwriter\skills\feishu-knowledge-store\scripts\config.py plugins\picturebook-screenwriter\config\feishu-knowledge-base.example.json plugins\picturebook-screenwriter\skills\feishu-knowledge-store\scripts\test_config.py
git commit -m "feat: add feishu config v2 and bootstrap state"
```

---

### Task 6: Windows-compatible reporting and final regression

**Goal:** Make real-world repeatable calls stable on Windows and generate machine-readable reports.

**Files:**
- Modify: `plugins\picturebook-screenwriter\skills\feishu-knowledge-store\scripts\sync_runner.py`
- Modify: `plugins\picturebook-screenwriter\README.md`
- Test: extend `test_sync_runner.py`

**Requirement:** every phase writes a UTF-8 JSON report.

```json
{
  "run_id": "20260917-sync",
  "status": "published",
  "source": {
    "document_count": 8,
    "container_count": 3
  },
  "candidates": 6,
  "published": 6,
  "preserved": 0,
  "queued": 0,
  "failed": 0
}
```

- [x] **Step 1: Set subprocess env to UTF-8.**

```python
subprocess_env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
```

- [x] **Step 2: Use `Path` for all file and temp paths.**

- [x] **Step 3: Add Windows double-quote escaping.**

- [x] **Step 4: Add test case with Chinese path and Chinese title.**

- [x] **Step 5: Run the focused and full suites.**

```powershell
python -m unittest plugins\picturebook-screenwriter\skills\feishu-knowledge-store -v
python -m unittest plugins\picturebook-screenwriter\skills\wiki-ingest -v
```

- [x] **Step 6: Commit.**

```powershell
git add plugins\picturebook-screenwriter\skills\feishu-knowledge-store plugins\picturebook-screenwriter\skills\wiki-ingest plugins\picturebook-screenwriter\README.md
git commit -m "feat: add runtime hardening and reports"
```

---

## Live-environment acceptance

This plan is complete only when all pass:

1. `lark-cli 1.0.95` version check succeeds.
2. `node-list --space-id` returns 3 top-level source nodes.
3. `docs +fetch` reads back target control page and content page without manual normalization.
4. `prepare` creates a run-dir but does not write target.
5. `publish` acquires and releases the lock.
6. `verify` returns a verified report.
7. First run completes from a clean target.
8. Second run with no source change writes zero pages.
9. Human edit survives an AI source update.
10. Human deletion is not reintroduced.
11. Revision conflict is detected and queued.
12. Interrupted bootstrap can resume.
13. Windows environment does not throw UnicodeDecodeError.

## Plan self-review

| Requirement | Covered by |
| --- | --- |
| CLI 1.0.95 compatibility | Task 1 |
| Read/write stability and `_notice` handling | Tasks 1 and 2 |
| Remote Markdown stability | Task 2 |
| Unified candidate schema | Task 3 |
| Stable end-to-end execution | Task 4 |
| Bootstrap recovery | Task 5 |
| Config semantics and root mode | Task 5 |
| Windows reporting and reproducibility | Task 6 |

This plan intentionally does **not** rewrite the human-priority safety model or change the target Wiki pattern. It focuses on runtime stabilization.
