# Feishu Knowledge Config Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

Revision: v2, portable multi-user discovery

**Goal:** Make the Feishu knowledge configuration durable, portable, and discoverable across users, operating systems, project subdirectories, and plugin upgrades; add read-only authority retrieval and explicitly opt-in offline evidence.

**Architecture:** A shared resolver owns configuration-path discovery: direct paths, environment override, nearest workspace ancestor, and platform-standard user directories. `store_cli` and `sync_runner` never derive workspace from the plugin script location. `authority_cli.py` is the standard read-only retrieval entry point and writes the last confirmed evidence cache only after a successful, complete remote read. Lark CLI lookup uses portable environment and standard installation locations.

**Tech Stack:** Python 3.10+, standard-library `unittest`, JSON, existing Lark CLI adapter, existing Feishu control-plane contracts.

**Spec:** `docs/superpowers/specs/2026-09-21-feishu-knowledge-config-discovery-design.md`

## Implementation Status

- Revision 1, Tasks 1-6: implemented and merged to `main` through commit `00db22a`.
- Task 7: manual acceptance was not executed because it requires a user-approved real target token.
- Revision 2, Tasks 8-11: pending. These tasks supersede the v1 discovery order, documentation examples, and CLI fallbacks.

## Global Constraints

- Configuration schema remains schema version 2.
- Discovery order is explicit `--config`, `PICTUREBOOK_KB_CONFIG`, nearest workspace ancestor, platform-standard user directories, then deprecated `~/.picturebook-screenwriter`.
- Workspace chain discovery does not recurse downward into child directories.
- User paths resolve from injected `APPDATA`, `XDG_CONFIG_HOME`, `HOME`, and platform rules, not from an author's real home directory.
- Lark CLI lookup uses `LARK_CLI_PATH`, `PATH`, and standard npm-global locations; it has no `C:\lark-cli` or `D:\lark-cli` fallback.
- Do not discover `config/feishu-knowledge-base.example.json`.
- Do not infer workspace from plugin script location.
- Do not use offline cache unless the caller explicitly opts in.
- Offline evidence is non-authoritative and must be labeled as such.
- Do not store, log, or commit real Feishu tokens.
- Implement in the source repository under `plugins/picturebook-screenwriter`; release Revision 2 as version `0.3.2`.

## Review Focus

1. A missing direct `--config` path must fail instead of silently trying other candidates; pinned by Task 2.
2. A placeholder target token must never be treated as configured; pinned by Task 1.
3. An installed plugin cache example must never become live configuration; pinned by Task 1.
4. A project subdirectory must discover the nearest ancestor config without reading unrelated child projects; pinned by Task 8.
5. Platform user directories must be resolved from injected environment values, not the author's real home; pinned by Task 8.
6. Lark CLI discovery must have no author-specific drive-letter fallback; pinned by Task 9.
7. Remote authority failure without explicit opt-in must fail closed, not silently use local cache; pinned by Task 4.
8. `config-status` must not leak token values; pinned by Task 2.

---

## File Structure

- Create `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/config_paths.py`: configuration-path resolution and resolution errors.
- Modify `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/config.py`: schema validation, placeholder rejection, delegation to the resolver when no explicit path is supplied.
- Modify `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/store_cli.py`: workspace-aware components, `config-status`, machine-readable errors.
- Modify `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/sync_runner.py`: optional explicit config and workspace discovery.
- Modify `plugins/picturebook-screenwriter/skills/knowledge-loader/scripts/authority.py`: optional cache injection and offline opt-in.
- Create `plugins/picturebook-screenwriter/skills/knowledge-loader/scripts/authority_cli.py`: standard read-only CLI.
- Modify `plugins/picturebook-screenwriter/skills/knowledge-loader/scripts/load_knowledge.py`: public bundle serialization helpers.
- Modify plugin documentation and version files.

## Task 1: Config Resolver and Placeholder Rejection

**Files:**
- Create: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/config_paths.py`
- Modify: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/config.py`
- Test: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_config_paths.py`
- Test: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_config.py`

**Interfaces:**
- Consumes: existing `KnowledgeConfig` from `config.py`.
- Produces: `ResolvedConfigPath(path: Path, origin: str, searched: tuple[Path, ...])`, `resolve_config_path(explicit_path, workspace, environ, home=None)`, `ConfigResolutionError(status, searched, origin, message)`.

- [ ] **Step 1: Write failing resolver tests**

Create `test_config_paths.py` with:

```python
import json
import tempfile
import unittest
from pathlib import Path

from config_paths import ConfigResolutionError, resolve_config_path


class ResolverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()

    def write(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
        return path

    def test_explicit_path_has_highest_priority(self):
        explicit = self.write(self.root / "explicit.json")
        environment = self.write(self.root / "environment.json")
        workspace = self.write(self.workspace / "feishu-knowledge-base.json")
        resolved = resolve_config_path(
            explicit, self.workspace,
            {"PICTUREBOOK_KB_CONFIG": str(environment)}, home=self.root,
        )
        self.assertEqual(resolved.path, explicit)
        self.assertEqual(resolved.origin, "explicit")

    def test_environment_precedes_workspace(self):
        environment = self.write(self.root / "environment.json")
        workspace = self.write(self.workspace / "feishu-knowledge-base.json")
        resolved = resolve_config_path(
            None, self.workspace,
            {"PICTUREBOOK_KB_CONFIG": str(environment)}, home=self.root,
        )
        self.assertEqual(resolved.path, environment)
        self.assertEqual(resolved.origin, "environment")

    def test_workspace_precedes_user_file(self):
        workspace = self.write(self.workspace / "feishu-knowledge-base.json")
        user = self.write(self.root / ".picturebook-screenwriter" / "feishu-knowledge-base.json")
        resolved = resolve_config_path(None, self.workspace, {}, home=self.root)
        self.assertEqual(resolved.path, workspace)
        self.assertEqual(resolved.origin, "workspace")
        self.assertIn(user, resolved.searched)

    def test_missing_direct_path_does_not_fall_back(self):
        workspace = self.write(self.workspace / "feishu-knowledge-base.json")
        with self.assertRaises(ConfigResolutionError) as caught:
            resolve_config_path(
                self.root / "missing.json", self.workspace, {}, home=self.root,
            )
        self.assertEqual(caught.exception.status, "missing_config")
        self.assertEqual(caught.exception.searched, (self.root / "missing.json",))

    def test_missing_discovery_reports_all_fallback_candidates(self):
        user = self.root / ".picturebook-screenwriter" / "feishu-knowledge-base.json"
        with self.assertRaises(ConfigResolutionError) as caught:
            resolve_config_path(None, self.workspace, {}, home=self.root)
        self.assertEqual(
            caught.exception.searched,
            (self.workspace / "feishu-knowledge-base.json", user),
        )
```

- [ ] **Step 2: Run the new tests and verify they fail**

Run:

```powershell
python .\plugins\picturebook-screenwriter\skills\feishu-knowledge-store\scripts\test_config_paths.py
```

Expected: FAIL because `config_paths` does not exist.

- [ ] **Step 3: Implement the resolver**

Create `config_paths.py` with:

```python
"""Portable resolution paths for Feishu knowledge-base configuration."""

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class ResolvedConfigPath:
    path: Path
    origin: str
    searched: tuple[Path, ...]


class ConfigResolutionError(ValueError):
    def __init__(self, message: str, *, status: str = "missing_config",
                 searched: tuple[Path, ...] = (), origin: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.searched = searched
        self.origin = origin

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "error": str(self),
            "origin": self.origin,
            "searched": [str(path) for path in self.searched],
        }


def resolve_config_path(
    explicit_path: str | Path | None,
    workspace: str | Path | None,
    environ: Mapping[str, str] | None = None,
    home: str | Path | None = None,
) -> ResolvedConfigPath:
    environment = os.environ if environ is None else environ
    direct = explicit_path
    if direct is None:
        direct = environment.get("PICTUREBOOK_KB_CONFIG") or None
    if direct is not None:
        path = Path(direct)
        if path.is_file():
            return ResolvedConfigPath(path, "explicit" if explicit_path is not None else "environment", (path,))
        raise ConfigResolutionError(f"configuration file not found: {path}", searched=(path,))

    base = Path(workspace) if workspace is not None else Path.cwd()
    home_path = Path(home) if home is not None else Path.home()
    candidates = (
        (base / "feishu-knowledge-base.json", "workspace"),
        (home_path / ".picturebook-screenwriter" / "feishu-knowledge-base.json", "user"),
    )
    searched = tuple(path for path, _ in candidates)
    for path, origin in candidates:
        if path.is_file():
            return ResolvedConfigPath(path, origin, searched)
    raise ConfigResolutionError(
        "configuration file not found; create <workspace>/feishu-knowledge-base.json "
        "or set PICTUREBOOK_KB_CONFIG",
        searched=searched,
    )
```

- [ ] **Step 4: Update config loading**

In `config.py`, add:

```python
from config_paths import ConfigResolutionError, resolve_config_path

PLACEHOLDER_TARGET_TOKEN = "REPLACE_WITH_TARGET_ROOT_TOKEN"


class ConfigError(ValueError):
    status = "invalid_config"

    def to_dict(self) -> dict:
        return {"status": self.status, "error": str(self)}


class PlaceholderTargetTokenError(ConfigError):
    status = "placeholder_target_token"
```

Change `load_config()` to use `config_path=None` as optional and route no-explicit-path loading through the resolver:

```python
def load_config(
    config_path: str | os.PathLike[str] | None,
    workspace: str | os.PathLike[str] | None = None,
    environ: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> KnowledgeConfig:
    environment = os.environ if environ is None else environ
    if config_path is None:
        resolved = resolve_config_path(None, workspace, environment)
        selected = resolved.path
    else:
        selected = Path(config_path)
    path = Path(selected)
```

After validating `target_root_token`, reject the placeholder before constructing `KnowledgeConfig`:

```python
    if target_root_token == PLACEHOLDER_TARGET_TOKEN:
        raise PlaceholderTargetTokenError(
            "target.root_token must not remain the placeholder value"
        )
```

Update the existing config tests to use `"root-token"` as the valid target token and add:

```python
    def test_placeholder_target_token_is_rejected(self):
        with self.assertRaisesRegex(ConfigError, "placeholder"):
            self.load(config_json({}), {})
```

Replace the existing `test_environment_path_wins` with:

```python
    def test_environment_is_used_without_explicit_config(self):
        path = self.tmp / "from-environment.json"
        with patch("config.Path.read_text", return_value=config_json({})):
            config = load_config(None, self.tmp, {"PICTUREBOOK_KB_CONFIG": str(path)})
        self.assertEqual(config.source.root_mode, "space")
```

- [ ] **Step 5: Run focused tests**

Run:

```powershell
python .\plugins\picturebook-screenwriter\skills\feishu-knowledge-store\scripts\test_config_paths.py
python .\plugins\picturebook-screenwriter\skills\feishu-knowledge-store\scripts\test_config.py
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/config_paths.py `
  plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/config.py `
  plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_config_paths.py `
  plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_config.py
git commit -m "feat(feishu): add workspace config resolution"
```

## Task 2: Store CLI Workspace Discovery and Config Status

**Files:**
- Modify: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/store_cli.py`
- Test: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_store_cli.py`

**Interfaces:**
- Consumes: `resolve_config_path()`, `ConfigResolutionError`, `PlaceholderTargetTokenError`, `ensure_lark_cli()`.
- Produces: `build_components(config_path=None, environ=None, workspace=None)` and `store_cli config-status`.

- [ ] **Step 1: Write failing CLI discovery and status tests**

In `test_store_cli.py`, change every fake factory signature from:

```python
def fake_factory(config_path, environ=None):
```

to:

```python
def fake_factory(config_path, environ=None, workspace=None):
```

Apply the same signature change to the local `missing_factory`, `factory`, and anonymous factories. Add tests:

```python
    def test_components_use_workspace_without_explicit_config(self):
        workspace = Path(self.tmp.name)
        workspace_config = workspace / "feishu-knowledge-base.json"
        write_config(workspace_config)
        seen = {}

        def factory(config_path, environ=None, workspace=None):
            seen.update(config_path=config_path, workspace=workspace)
            return SimpleNamespace(
                config=config(workspace_config), cli=FakeCli(),
                publisher=FakePublisher(), control_plane=FakeControlPlane(),
            )

        stdout = StringIO()
        exit_code = store_cli.main(
            ["resolve", "--workspace", str(workspace)],
            stdout=stdout, components_factory=factory,
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(Path(seen["config_path"]), workspace_config)
        self.assertEqual(seen["workspace"], workspace)

    def test_missing_config_error_is_machine_readable(self):
        stdout = StringIO()
        exit_code = store_cli.main(
            ["resolve", "--workspace", str(self.tmp.name)],
            stdout=stdout,
            components_factory=store_cli.build_components,
        )
        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 1)
        self.assertEqual(payload["status"], "missing_config")
        self.assertIn("searched", payload)

    def test_config_status_reports_missing_configuration(self):
        stdout = StringIO()
        with patch.object(store_cli, "ensure_lark_cli", return_value={
            "status": "available", "path": "D:\\lark-cli\\lark-cli.exe",
            "version": "1.0.95",
        }):
            exit_code = store_cli.main(
                ["config-status", "--workspace", str(self.tmp.name)],
                stdout=stdout,
            )
        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertFalse(payload["configured"])
        self.assertEqual(payload["status"], "missing_config")
        self.assertEqual(payload["cli_status"]["version"], "1.0.95")

    def test_config_status_reports_placeholder_without_remote_calls(self):
        stdout = StringIO()
        with patch.object(store_cli, "ensure_lark_cli", return_value={
            "status": "available", "path": "D:\\lark-cli\\lark-cli.exe",
            "version": "1.0.95",
        }):
            exit_code = store_cli.main(
                ["config-status", "--config", str(self.config_path)],
                stdout=stdout,
            )
        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertFalse(payload["configured"])
        self.assertEqual(payload["status"], "placeholder_target_token")
        self.assertNotIn("root-token", stdout.getvalue())
```

The default test config should keep `"REPLACE_WITH_TARGET_ROOT_TOKEN"` specifically for this status test.

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```powershell
python .\plugins\picturebook-screenwriter\skills\feishu-knowledge-store\scripts\test_store_cli.py
```

Expected: FAIL because workspace discovery and `config-status` do not exist.

- [ ] **Step 3: Implement components and config status**

In `store_cli.py`, replace the current `build_components()` with:

```python
from config import ConfigError, load_config
from config_paths import ConfigResolutionError, ResolvedConfigPath, resolve_config_path
from lark_cli_bootstrap import ensure_lark_cli


@dataclass(frozen=True)
class Components:
    config: Any
    config_path: Path
    cli: Any
    publisher: Publisher
    control_plane: ControlPlane


def build_components(config_path=None, environ=None, workspace=None) -> Components:
    resolved = resolve_config_path(config_path, workspace, environ)
    config = load_config(resolved.path, resolved.path.parent, environ)
    cli = LarkCli(config.cli_candidates[0], identity=config.identity)
    publisher = Publisher(cli, config.target.root_token, config.target.space_id)
    tokens = publisher.resolve_control_plane()
    control_plane = ControlPlane(
        cli,
        {"index": tokens["index"], "lock": tokens["lock"]},
        lock_ttl_minutes=config.lock_ttl_minutes,
    )
    return Components(config, resolved.path, cli, publisher, control_plane)
```

Update parser arguments so every subcommand accepts optional `--config` and `--workspace`, and add:

```python
    status = commands.add_parser("config-status")
    status.add_argument("--config")
    status.add_argument("--workspace")
```

Add a read-only status helper:

```python
def config_status(args, stdout, environ=None, cli_probe=None) -> int:
    cli_probe = cli_probe or ensure_lark_cli
    resolved = None
    config = None
    try:
        resolved = resolve_config_path(args.config, args.workspace, environ)
        config = load_config(resolved.path, resolved.path.parent, environ)
        status = "configured"
        origin = resolved.origin
        path = str(resolved.path)
        searched = [str(item) for item in resolved.searched]
    except ConfigResolutionError as error:
        status = error.status
        origin = error.origin
        path = None
        searched = [str(item) for item in error.searched]
    except ConfigError as error:
        status = getattr(error, "status", "invalid_config")
        origin = resolved.origin if resolved is not None else None
        path = str(resolved.path) if resolved is not None else (
            str(args.config) if args.config else None
        )
        searched = []

    try:
        cli_status = cli_probe(
            candidates=config.cli_candidates if config is not None else None,
            environ=environ,
        )
    except Exception as error:
        cli_status = {"status": "error", "message": str(error)}

    payload = {
        "configured": status == "configured",
        "status": status,
        "origin": origin,
        "path": path,
        "searched": searched,
        "cli_status": cli_status,
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=stdout)
    return 0
```

In `main()`, handle `config-status` before constructing components:

```python
    if args.command == "config-status":
        return config_status(args, stdout, environ=None)
```

Replace the normal factory invocation with:

```python
    components = factory(args.config, environ=None, workspace=args.workspace)
```

Replace every later `args.config` passed to `SyncRunner` with `components.config_path`, and pass `workspace=args.workspace`:

```python
    runner = SyncRunner(
        components.config_path, components.cli, components.publisher,
        components.control_plane, config=components.config,
        workspace=args.workspace,
    )
```

Replace the exception serializer with:

```python
    except Exception as error:
        payload = error.to_dict() if hasattr(error, "to_dict") else {
            "status": "runtime_error", "error": str(error),
        }
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=stdout)
        return 1
```

- [ ] **Step 4: Run focused tests**

Run:

```powershell
python .\plugins\picturebook-screenwriter\skills\feishu-knowledge-store\scripts\test_store_cli.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/store_cli.py `
  plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_store_cli.py
git commit -m "feat(feishu): add workspace config status"
```

## Task 3: Sync Runner Optional Config

**Files:**
- Modify: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/sync_runner.py`
- Test: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_sync_runner.py`

**Interfaces:**
- Consumes: `resolve_config_path()` and `load_config()`.
- Produces: `SyncRunner(config_path, cli, publisher=None, control_plane=None, config=None, workspace=None, environ=None)`.

- [ ] **Step 1: Write failing workspace discovery test**

In `test_sync_runner.py`, change the setup config target token to `"root-token"` so it is valid after Task 1. Add:

```python
    def test_prepare_discovers_workspace_config_without_explicit_config(self):
        self._write_manifest()
        workspace_config = Path(self.tmp.name) / "feishu-knowledge-base.json"
        workspace_config.write_text(self.config_path.read_text(encoding="utf-8"), encoding="utf-8")
        runner = SyncRunner(
            None, FakeCli(), publisher=FakePublisher(),
            control_plane=FakeControlPlane(),
            workspace=Path(self.tmp.name), environ={},
        )
        runner.prepare(self.run_dir)
        self.assertEqual(runner.config_path, workspace_config)
```

- [ ] **Step 2: Run the test and verify it fails**

Run:

```powershell
python .\plugins\picturebook-screenwriter\skills\feishu-knowledge-store\scripts\test_sync_runner.py
```

Expected: FAIL because `SyncRunner` cannot accept `config_path=None`.

- [ ] **Step 3: Implement optional config**

In `sync_runner.py`, update imports and constructor:

```python
from config_paths import resolve_config_path


class SyncRunner:
    def __init__(self, config_path, cli, publisher=None, control_plane=None,
                 config=None, workspace=None, environ=None) -> None:
        self.config_path = Path(config_path) if config_path is not None else None
        self.workspace = Path(workspace) if workspace is not None else Path.cwd()
        self.environ = environ
        self.cli = cli
        self.publisher = publisher
        self.control_plane = control_plane
        self._config = config
        self.bootstrap_state = BootstrapState.REQUIRED
```

Update `_load_config()`:

```python
    def _load_config(self) -> Any:
        if self._config is not None:
            return self._config
        if self.config_path is None:
            resolved = resolve_config_path(None, self.workspace, self.environ)
            self.config_path = resolved.path
        return load_config(self.config_path, self.workspace, self.environ)
```

Update standalone parser and invocation:

```python
    parser.add_argument("--config")
    parser.add_argument("--workspace")
    runner = SyncRunner(args.config, None, workspace=args.workspace)
```

- [ ] **Step 4: Run focused tests**

Run:

```powershell
python .\plugins\picturebook-screenwriter\skills\feishu-knowledge-store\scripts\test_sync_runner.py
python .\plugins\picturebook-screenwriter\skills\feishu-knowledge-store\scripts\test_store_cli.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/sync_runner.py `
  plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_sync_runner.py
git commit -m "feat(feishu): discover sync config from workspace"
```

## Task 4: Authority Cache Injection

**Files:**
- Modify: `plugins/picturebook-screenwriter/skills/knowledge-loader/scripts/authority.py`
- Modify: `plugins/picturebook-screenwriter/skills/knowledge-loader/scripts/load_knowledge.py`
- Test: `plugins/picturebook-screenwriter/skills/knowledge-loader/scripts/test_authority.py`

**Interfaces:**
- Consumes: existing `KnowledgeLoader`, `KnowledgeCache`, and evidence dataclasses.
- Produces: `AuthorityLoader(control_plane, cli, cache_store=None, cached_bundle=None)` and `AuthorityLoader.load(query, allow_offline_cache=False)`; public `bundle_to_dict()` and `bundle_from_dict()`.

- [ ] **Step 1: Write failing cache tests**

Add to `test_authority.py`:

```python
from load_knowledge import KnowledgeEvidence, KnowledgeEvidenceBundle


class RecordingCache:
    def __init__(self):
        self.saved = None

    def save(self, bundle):
        self.saved = bundle


class AuthorityCacheTests(unittest.TestCase):
    def test_successful_remote_load_saves_cache(self):
        cache = RecordingCache()
        loader = AuthorityLoader(FakeControlPlane(), FakeCli(), cache_store=cache)
        bundle = loader.load(AuthorityQuery("小老鼠迈尔斯", "海外绘本", ("worldview",)))
        self.assertFalse(bundle.offline)
        self.assertEqual(cache.saved["items"][0]["key"], "海外绘本/小老鼠迈尔斯/worldview")

    def test_remote_failure_uses_cache_only_with_explicit_opt_in(self):
        class FailingPlane:
            def read_index(self):
                raise RuntimeError("network down")

        item = KnowledgeEvidence(
            key="海外绘本/小老鼠迈尔斯/worldview", doc_token="doc-a",
            revision_id=42, title="worldview", content="正文",
            source_revisions={"node-a": "17"},
        )
        cached = KnowledgeEvidenceBundle(
            items=(item,), warnings=("缓存",), offline=False,
            fetched_at="2026-09-20T10:00:00+08:00",
        )
        loader = AuthorityLoader(
            FailingPlane(), FakeCli(), cached_bundle=cached,
        )
        bundle = loader.load(
            AuthorityQuery("小老鼠迈尔斯", "海外绘本", ("worldview",)),
            allow_offline_cache=True,
        )
        self.assertTrue(bundle.offline)
        self.assertIn("非权威", bundle.warnings[0])

        strict = AuthorityLoader(FailingPlane(), FakeCli(), cached_bundle=cached)
        with self.assertRaisesRegex(RuntimeError, "network down"):
            strict.load(AuthorityQuery("小老鼠迈尔斯", "海外绘本", ("worldview",)))
```

- [ ] **Step 2: Run the test and verify it fails**

Run:

```powershell
python .\plugins\picturebook-screenwriter\skills\knowledge-loader\scripts\test_authority.py
```

Expected: FAIL because `AuthorityLoader` does not accept cache arguments.

- [ ] **Step 3: Implement public bundle helpers and cache injection**

In `load_knowledge.py`, rename `_bundle_to_dict()` to `bundle_to_dict()` and add a public reverse helper:

```python
def bundle_from_dict(raw: dict[str, Any] | None) -> KnowledgeEvidenceBundle | None:
    if raw is None:
        return None
    return KnowledgeEvidenceBundle(
        items=tuple(KnowledgeEvidence(**item) for item in raw.get("items", [])),
        warnings=tuple(raw.get("warnings", [])),
        offline=bool(raw.get("offline", False)),
        fetched_at=raw["fetched_at"],
    )
```

Update the internal save call to `bundle_to_dict(bundle)`.

Change the existing offline warning to:

```python
warnings.insert(0, "目标飞书知识库不可用，正在使用最后确认的本地缓存；它是非权威版本。")
```

In `authority.py`, change the loader:

```python
class AuthorityLoader:
    def __init__(self, control_plane, cli, cache_store=None, cached_bundle=None) -> None:
        self.loader = KnowledgeLoader(
            control_plane, cli,
            cache_store=cache_store, cached_bundle=cached_bundle,
        )

    def load(self, query: AuthorityQuery, allow_offline_cache: bool = False):
        bundle = self.loader.load(
            KnowledgeQuery(
                project_id=query.project_id,
                series_id=query.series_id,
                page_types=query.page_types,
                limit=len(query.page_types) * 2,
            ),
            allow_offline_cache=allow_offline_cache,
        )
        found = {item.key.split("/")[-1] for item in bundle.items}
        missing = [page_type for page_type in query.page_types if page_type not in found]
        if missing:
            raise AuthorityGapError("缺少权威知识页：" + "、".join(missing))
        return bundle
```

The offline warning text must contain `非权威` and `最后确认的本地缓存`.

- [ ] **Step 4: Run focused tests**

Run:

```powershell
python .\plugins\picturebook-screenwriter\skills\knowledge-loader\scripts\test_authority.py
python .\plugins\picturebook-screenwriter\skills\knowledge-loader\scripts\test_load_knowledge.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add plugins/picturebook-screenwriter/skills/knowledge-loader/scripts/authority.py `
  plugins/picturebook-screenwriter/skills/knowledge-loader/scripts/load_knowledge.py `
  plugins/picturebook-screenwriter/skills/knowledge-loader/scripts/test_authority.py
git commit -m "feat(loader): support authority evidence cache"
```

## Task 5: Read-Only Authority CLI

**Files:**
- Create: `plugins/picturebook-screenwriter/skills/knowledge-loader/scripts/authority_cli.py`
- Test: `plugins/picturebook-screenwriter/skills/knowledge-loader/scripts/test_authority_cli.py`

**Interfaces:**
- Consumes: `resolve_config_path()`, `load_config()`, `build_components()`, `ensure_lark_cli()`, `AuthorityLoader`, `KnowledgeCache`, and bundle helpers.
- Produces: `authority_cli.py load --project-id ... --series-id ... --page-types ... [--workspace ...] [--config ...] [--allow-offline-cache]`.

- [ ] **Step 1: Write failing CLI tests**

Create `test_authority_cli.py` with:

```python
import json
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

from authority_cli import build_parser, main
from load_knowledge import KnowledgeEvidence, KnowledgeEvidenceBundle
from page_codec import render_remote_page


class FakePlane:
    def read_index(self):
        from models import IndexEntry
        return {
            "海外绘本/小老鼠迈尔斯/worldview": IndexEntry(
                key="海外绘本/小老鼠迈尔斯/worldview", doc_token="doc-world",
                wiki_node_token="node-world", source_revisions={"source": "r1"},
                last_ai_revision_id=42, last_seen_revision_id=42,
                status="published",
            )
        }


class FakeCli:
    def fetch_doc(self, token):
        content = render_remote_page("# 世界观\n\n正文", {
            "key": "海外绘本/小老鼠迈尔斯/worldview", "page_type": "worldview",
            "source_node_tokens": ["source"],
            "source_revisions": {"source": "r1"},
            "last_ai_revision_id": 42,
        })
        return {"data": {"document": {"revision_id": 42, "content": content}}}


def valid_config(path: Path):
    path.write_text(json.dumps({
        "schema_version": 2,
        "source": {"space_id": "source-space", "root_mode": "space",
                   "wiki_url": "https://example.feishu.cn/wiki/source"},
        "target": {"space_id": "target-space", "root_token": "root-token"},
        "identity": "user", "lock_ttl_minutes": 45,
    }), encoding="utf-8")


class AuthorityCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config_path = self.root / "feishu-knowledge-base.json"
        valid_config(self.config_path)

    def test_load_returns_remote_evidence_and_writes_cache(self):
        def factory(config_path, environ=None, workspace=None):
            return SimpleNamespace(
                config=SimpleNamespace(cli_candidates=("lark-cli",)),
                cli=FakeCli(), publisher=None, control_plane=FakePlane(),
            )

        stdout = StringIO()
        exit_code = main([
            "load", "--project-id", "小老鼠迈尔斯", "--series-id", "海外绘本",
            "--page-types", "worldview", "--workspace", str(self.root),
            "--config", str(self.config_path),
        ], stdout=stdout, components_factory=factory, cli_probe=lambda **kwargs: {
            "status": "available", "version": "1.0.95",
        }, getuser=lambda: "tester")
        payload = json.loads(stdout.getvalue())
        cache = self.root / ".picturebook-screenwriter" / "cache" / "tester" / "knowledge-bundle.json"
        self.assertEqual(exit_code, 0)
        self.assertFalse(payload["offline"])
        self.assertEqual(payload["items"][0]["key"], "海外绘本/小老鼠迈尔斯/worldview")
        self.assertTrue(cache.exists())

    def test_remote_failure_requires_explicit_opt_in(self):
        def factory(config_path, environ=None, workspace=None):
            raise RuntimeError("network down")

        stdout = StringIO()
        exit_code = main([
            "load", "--project-id", "p", "--series-id", "s",
            "--page-types", "worldview", "--workspace", str(self.root),
            "--config", str(self.config_path),
        ], stdout=stdout, components_factory=factory,
            cli_probe=lambda **kwargs: {"status": "available"})
        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 1)
        self.assertEqual(payload["status"], "runtime_error")

    def test_missing_config_is_actionable(self):
        stdout = StringIO()
        exit_code = main([
            "load", "--project-id", "p", "--series-id", "s",
            "--page-types", "worldview", "--workspace", str(self.root),
        ], stdout=stdout, components_factory=lambda *args, **kwargs: None)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 1)
        self.assertEqual(payload["status"], "missing_config")
        self.assertIn("searched", payload)
```

- [ ] **Step 2: Run the test and verify it fails**

Run:

```powershell
python .\plugins\picturebook-screenwriter\skills\knowledge-loader\scripts\test_authority_cli.py
```

Expected: FAIL because `authority_cli` does not exist.

- [ ] **Step 3: Implement the CLI**

Create `authority_cli.py`:

```python
"""Read-only CLI for the Feishu authority knowledge loader."""

import argparse
from pathlib import Path
import getpass
import json
import re
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
STORE_SCRIPTS = SCRIPT_DIR.parent.parent / "feishu-knowledge-store" / "scripts"
for scripts_path in (STORE_SCRIPTS, SCRIPT_DIR):
    if str(scripts_path) not in sys.path:
        sys.path.insert(0, str(scripts_path))

from authority import AuthorityGapError, AuthorityLoader, AuthorityQuery
from cache import KnowledgeCache
from config import load_config
from config_paths import resolve_config_path
from lark_cli_bootstrap import ensure_lark_cli
from load_knowledge import bundle_from_dict, bundle_to_dict
from store_cli import build_components


def safe_user(getuser=None) -> str:
    try:
        raw = (getuser or getpass.getuser)()
    except Exception:
        return "local-user"
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(raw)).strip("._")
    return value or "local-user"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read Feishu authority knowledge")
    commands = parser.add_subparsers(dest="command", required=True)
    load = commands.add_parser("load")
    load.add_argument("--project-id", required=True)
    load.add_argument("--series-id", required=True)
    load.add_argument("--page-types", required=True)
    load.add_argument("--workspace")
    load.add_argument("--config")
    load.add_argument("--allow-offline-cache", action="store_true")
    return parser


def _error_payload(error: Exception) -> dict:
    if hasattr(error, "to_dict"):
        return error.to_dict()
    return {"status": "runtime_error", "error": str(error)}


def main(argv=None, stdout=None, components_factory=None, cli_probe=None,
         getuser=None, environ=None) -> int:
    stdout = stdout if stdout is not None else sys.stdout
    args = build_parser().parse_args(argv)
    components_factory = components_factory or build_components
    cli_probe = cli_probe or ensure_lark_cli
    workspace = Path(args.workspace) if args.workspace else Path.cwd()
    try:
        resolved = resolve_config_path(args.config, workspace, environ)
        config = load_config(resolved.path, resolved.path.parent, environ)
        cache_store = KnowledgeCache(workspace, safe_user(getuser))
        cached_bundle = bundle_from_dict(cache_store.load())
        page_types = tuple(
            value.strip() for value in args.page_types.split(",") if value.strip()
        )
        query = AuthorityQuery(args.project_id, args.series_id, page_types)
        try:
            cli_status = cli_probe(candidates=config.cli_candidates, environ=environ)
            if cli_status.get("status") != "available":
                if not (args.allow_offline_cache and cached_bundle is not None):
                    raise RuntimeError("lark-cli is unavailable")
            components = components_factory(
                resolved.path, environ=environ, workspace=workspace,
            )
            loader = AuthorityLoader(
                components.control_plane, components.cli,
                cache_store=cache_store, cached_bundle=cached_bundle,
            )
            bundle = loader.load(query, allow_offline_cache=args.allow_offline_cache)
        except Exception as error:
            if not (args.allow_offline_cache and cached_bundle is not None):
                raise
            from load_knowledge import KnowledgeEvidenceBundle
            bundle = KnowledgeEvidenceBundle(
                items=cached_bundle.items,
                warnings=(
                    "目标飞书知识库不可用，正在使用最后确认的本地缓存；"
                    "它不是权威版本。",
                    *cached_bundle.warnings,
                ),
                offline=True,
                fetched_at=cached_bundle.fetched_at,
            )
        payload = bundle_to_dict(bundle)
        payload["cache_path"] = str(cache_store.path)
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=stdout)
        return 0
    except Exception as error:
        print(json.dumps(_error_payload(error), ensure_ascii=False, sort_keys=True), file=stdout)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run focused tests**

Run:

```powershell
python .\plugins\picturebook-screenwriter\skills\knowledge-loader\scripts\test_authority_cli.py
python .\plugins\picturebook-screenwriter\skills\knowledge-loader\scripts\test_authority.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add plugins/picturebook-screenwriter/skills/knowledge-loader/scripts/authority_cli.py `
  plugins/picturebook-screenwriter/skills/knowledge-loader/scripts/test_authority_cli.py
git commit -m "feat(loader): add read-only authority cli"
```

## Task 6: Documentation, Version, and Offline Gates

**Files:**
- Modify: `plugins/picturebook-screenwriter/README.md`
- Modify: `plugins/picturebook-screenwriter/skills/knowledge-loader/SKILL.md`
- Modify: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/SKILL.md`
- Modify: `plugins/picturebook-screenwriter/.codex-plugin/plugin.json`
- Modify: `plugins/picturebook-screenwriter/tests/test_release_contract.py`
- Modify: `docs/CHANGELOG.md`

**Interfaces:**
- Consumes: all prior CLI contracts.
- Produces: plugin version `0.3.1` and documented configuration workflow.

- [ ] **Step 1: Add failing release and documentation assertions**

In `test_release_contract.py`, change both expected version assertions from `"0.3.0"` to `"0.3.1"`. Add:

```python
    def test_readme_documents_workspace_config_discovery(self):
        text = PLUGIN_README.read_text(encoding="utf-8")
        for value in (
            "feishu-knowledge-base.json",
            "PICTUREBOOK_KB_CONFIG",
            "authority_cli.py",
            "config-status",
            "非权威",
        ):
            with self.subTest(value=value):
                self.assertIn(value, text)

    def test_loader_skill_documents_explicit_offline_opt_in(self):
        text = (ROOT / "skills" / "knowledge-loader" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("authority_cli.py", text)
        self.assertIn("--allow-offline-cache", text)
        self.assertIn("非权威", text)
```

- [ ] **Step 2: Run release tests and verify they fail**

Run:

```powershell
python .\plugins\picturebook-screenwriter\tests\test_release_contract.py
```

Expected: FAIL because docs and version are not updated.

- [ ] **Step 3: Update documentation and version**

Update `.codex-plugin/plugin.json`:

```json
  "version": "0.3.1",
```

In `README.md`, replace the Feishu runtime section opening with documentation that states:

```markdown
### Configuration discovery

Put the real schema-v2 file at `<workspace>/feishu-knowledge-base.json`. `--config` overrides it, and `PICTUREBOOK_KB_CONFIG` is used when `--config` is omitted. The user fallback is `~/.picturebook-screenwriter/feishu-knowledge-base.json`. Plugin cache files, including `config/feishu-knowledge-base.example.json`, are templates and are never discovered.
```

Add these commands:

```powershell
python .\skills\feishu-knowledge-store\scripts\store_cli.py config-status --workspace <workspace>
python .\skills\knowledge-loader\scripts\authority_cli.py load --workspace <workspace> --project-id <project_id> --series-id <series_id> --page-types worldview,characters,content_spec
```

State that offline cache is non-authoritative and is read only with `--allow-offline-cache`.

In `knowledge-loader/SKILL.md`, add after the CLI bootstrap rule:

```markdown
2. Use `scripts/authority_cli.py load` as the standard entry point. Pass `--workspace` for the current project workspace. Add `--allow-offline-cache` only when the user explicitly approves offline fallback; its output is non-authoritative.
```

Renumber the following rules.

In `feishu-knowledge-store/SKILL.md`, add a configuration rule before the bootstrap rule:

```markdown
1. Resolve configuration in this order: explicit `--config`, `PICTUREBOOK_KB_CONFIG`, workspace root, then user fallback. Plugin cache is not durable configuration.
```

Renumber the following rules.

In `docs/CHANGELOG.md`, under `### Added`, append:

```markdown
- Workspace-first Feishu config discovery, config status, and read-only authority CLI.
```

- [ ] **Step 4: Run all relevant offline gates**

Run:

```powershell
python -m unittest discover -s .\plugins\picturebook-screenwriter\skills\feishu-knowledge-store\scripts -p "test_*.py"
python -m unittest discover -s .\plugins\picturebook-screenwriter\skills\knowledge-loader\scripts -p "test_*.py"
python .\plugins\picturebook-screenwriter\tests\test_release_contract.py
python <codex-home>\skills\.system\plugin-creator\scripts\validate_plugin.py .\plugins\picturebook-screenwriter
python .\scripts\governance_check.py
python .\scripts\package_check.py
```

Expected: all commands exit `0`.

- [ ] **Step 5: Scan for accidental secrets**

Run:

```powershell
git diff --check
git grep -n "REPLACE_WITH_TARGET_ROOT_TOKEN" -- plugins docs
```

Expected: whitespace check exits `0`; placeholder search matches only the documented example file and config status tests. Review the staged diff manually to confirm no real credential-shaped value is present.

- [ ] **Step 6: Commit**

```powershell
git add plugins/picturebook-screenwriter/README.md `
  plugins/picturebook-screenwriter/skills/knowledge-loader/SKILL.md `
  plugins/picturebook-screenwriter/skills/feishu-knowledge-store/SKILL.md `
  plugins/picturebook-screenwriter/.codex-plugin/plugin.json `
  plugins/picturebook-screenwriter/tests/test_release_contract.py `
  docs/CHANGELOG.md
git commit -m "docs: release feishu config discovery"
```

## Task 7: Manual Workspace Acceptance

**Files:**
- Create outside the repository: `<workspace>/feishu-knowledge-base.json`
- Read-only commands only; no repository file changes.

**Interfaces:**
- Consumes: released plugin scripts and a real target root token supplied by the user at runtime.
- Produces: local acceptance evidence and a cache file under the user workspace.

- [ ] **Step 1: Create workspace config from the example**

Copy the schema structure from `config/feishu-knowledge-base.example.json`, set the user-approved real target root token, and save it as `<workspace>/feishu-knowledge-base.json`. Do not paste the token into chat, logs, or repository files.

- [ ] **Step 2: Verify discovery without the environment variable**

Run:

```powershell
python .\skills\feishu-knowledge-store\scripts\store_cli.py config-status --workspace <workspace>
```

Expected JSON: `configured: true`, `status: "configured"`, `origin: "workspace"`, and no token value.

- [ ] **Step 3: Run a read-only authority load**

Run:

```powershell
python .\skills\knowledge-loader\scripts\authority_cli.py load --workspace <workspace> --project-id "小老鼠迈尔斯" --series-id "海外绘本" --page-types worldview,characters,content_spec
```

Expected: `offline: false`, required page keys present, and each item contains `revision_id` and `source_revisions`.

- [ ] **Step 4: Confirm cache exists**

Run:

```powershell
Test-Path "<workspace>\.picturebook-screenwriter\cache\<safe-user>\knowledge-bundle.json"
```

Expected: `True`.

- [ ] **Step 5: Record result**

Record command names, statuses, and page keys in the task summary. Do not record the token or the full evidence body.

## Task 8: Portable Workspace Chain and User Directories

**Files:**
- Modify: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/config_paths.py`
- Modify: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/store_cli.py`
- Test: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_config_paths.py`

**Interfaces:**
- Consumes: existing `resolve_config_path()` signature.
- Produces: `_workspace_chain(workspace)`, `user_config_candidates(environ, home=None, system=None)`, origin values `workspace`, `ancestor`, `user`, and `user-legacy`.

- [ ] **Step 1: Write failing ancestor and platform tests**

Add these representative tests to `test_config_paths.py`:

```python
    def test_nearest_ancestor_config_wins(self):
        root_config = self.write(self.root / "feishu-knowledge-base.json")
        nested = self.root / "shared" / "project"
        nested.mkdir(parents=True)
        resolved = resolve_config_path(None, nested, {}, home=self.root / "home")
        self.assertEqual(resolved.path, root_config)
        self.assertEqual(resolved.origin, "ancestor")

    def test_nearest_ancestor_beats_higher_ancestor(self):
        shared = self.root / "shared"
        shared.mkdir()
        self.write(shared / "feishu-knowledge-base.json")
        root_config = self.write(self.root / "feishu-knowledge-base.json")
        resolved = resolve_config_path(None, shared / "project", {}, home=self.root / "home")
        self.assertEqual(resolved.path, shared / "feishu-knowledge-base.json")
        self.assertNotEqual(resolved.path, root_config)

    def test_deeper_child_config_is_not_discovered(self):
        nested = self.workspace / "project"
        self.write(nested / "feishu-knowledge-base.json")
        with self.assertRaises(ConfigResolutionError):
            resolve_config_path(None, self.workspace, {}, home=self.root / "home")

    def test_windows_user_directory_is_portable(self):
        appdata = self.root / "appdata"
        expected = appdata / "picturebook-screenwriter" / "feishu-knowledge-base.json"
        self.write(expected)
        resolved = resolve_config_path(
            None, self.workspace, {"APPDATA": str(appdata)},
            home=self.root / "home", system="windows",
        )
        self.assertEqual(resolved.path, expected)
        self.assertEqual(resolved.origin, "user")

    def test_linux_xdg_user_directory_is_portable(self):
        config_home = self.root / "xdg"
        expected = config_home / "picturebook-screenwriter" / "feishu-knowledge-base.json"
        self.write(expected)
        resolved = resolve_config_path(
            None, self.workspace, {"XDG_CONFIG_HOME": str(config_home)},
            home=self.root / "home", system="linux",
        )
        self.assertEqual(resolved.path, expected)
        self.assertEqual(resolved.origin, "user")

    def test_macos_user_directory_is_portable(self):
        expected = self.root / "home" / "Library" / "Application Support" / \
            "picturebook-screenwriter" / "feishu-knowledge-base.json"
        self.write(expected)
        resolved = resolve_config_path(
            None, self.workspace, {}, home=self.root / "home", system="macos",
        )
        self.assertEqual(resolved.path, expected)
        self.assertEqual(resolved.origin, "user")
```

- [ ] **Step 2: Run the new tests and verify they fail**

Run:

```powershell
python .\plugins\picturebook-screenwriter\skills\feishu-knowledge-store\scripts\test_config_paths.py
```

Expected: FAIL because ancestor and platform directory discovery do not exist.

- [ ] **Step 3: Implement workspace chain and platform directories**

Update `config_paths.py`:

```python
_CONFIG_NAME = "feishu-knowledge-base.json"


def _workspace_chain(workspace: str | Path) -> tuple[Path, ...]:
    current = Path(workspace).expanduser().resolve()
    chain = []
    while True:
        chain.append(current)
        if current.parent == current:
            break
        current = current.parent
    return tuple(chain)


def user_config_candidates(
    environ: Mapping[str, str] | None = None,
    home: str | Path | None = None,
    system: str | None = None,
) -> tuple[Path, ...]:
    environment = os.environ if environ is None else environ
    home_path = Path(home) if home is not None else Path.home()
    system_name = (system or ("windows" if os.name == "nt" else "macos" if sys.platform == "darwin" else "linux")).lower()
    if system_name == "windows":
        appdata = environment.get("APPDATA") or (home_path / "AppData" / "Roaming")
        standard = Path(appdata) / "picturebook-screenwriter"
    elif system_name == "macos":
        standard = home_path / "Library" / "Application Support" / "picturebook-screenwriter"
    else:
        config_home = environment.get("XDG_CONFIG_HOME") or home_path / ".config"
        standard = Path(config_home) / "picturebook-screenwriter"
    legacy = home_path / ".picturebook-screenwriter"
    return (
        standard / _CONFIG_NAME,
        legacy / _CONFIG_NAME,
    )
```

Change workspace resolution from one exact path to the chain:

```python
    workspace_path = Path(workspace) if workspace is not None else Path.cwd()
    chain = _workspace_chain(workspace_path)
    for index, base in enumerate(chain):
        path = base / _CONFIG_NAME
        if path.is_file():
            origin = "workspace" if index == 0 else "ancestor"
            return ResolvedConfigPath(path, origin, tuple(base / _CONFIG_NAME for base in chain))
```

Then check `user_config_candidates(environment, home_path)`. Return origin `user` for the standard candidate and `user-legacy` for the legacy candidate. Keep direct-path failure behavior unchanged.

Add `import sys` to the module imports.

- [ ] **Step 4: Preserve provenance in config status**

No schema change is needed in `store_cli.py`; it passes `resolved.origin` through. Verify that status output can emit `ancestor`, `user`, and `user-legacy`.

- [ ] **Step 5: Run focused tests**

Run:

```powershell
python .\plugins\picturebook-screenwriter\skills\feishu-knowledge-store\scripts\test_config_paths.py
python .\plugins\picturebook-screenwriter\skills\feishu-knowledge-store\scripts\test_config.py
python .\plugins\picturebook-screenwriter\skills\feishu-knowledge-store\scripts\test_store_cli.py
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/config_paths.py `
  plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/store_cli.py `
  plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_config_paths.py
git commit -m "feat(feishu): discover portable workspace config"
```

## Task 9: Portable Lark CLI Lookup

**Files:**
- Modify: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/config.py`
- Modify: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/lark_cli_bootstrap.py`
- Test: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_config.py`
- Test: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_lark_cli_bootstrap.py`

**Interfaces:**
- Consumes: `LARK_CLI_PATH`, `PATH`, `APPDATA`, `HOME`, `ProgramFiles`.
- Produces: portable CLI candidates with no author-specific drive-letter fallback.

- [ ] **Step 1: Write failing portability tests**

Update CLI candidate tests to assert that machine-specific paths are absent:

```python
    def test_config_cli_candidates_use_standard_sources_only(self):
        config = self.load(config_json({}), {"LARK_CLI_PATH": "lark-cli"}, which=lambda _: "lark-cli")
        self.assertEqual(config.cli_candidates[0], Path("lark-cli"))
        self.assertEqual(config.cli_candidates[1], Path("lark-cli"))
        self.assertEqual(len(config.cli_candidates), 2)

    def test_bootstrap_platform_candidates_exclude_author_drive_letters(self):
        candidates = lark_cli_bootstrap._platform_candidates({
            "APPDATA": str(self.tmp / "appdata"),
            "ProgramFiles": str(self.tmp / "program-files"),
            "HOME": str(self.tmp / "home"),
        })
        text = "\n".join(str(path) for path in candidates)
        self.assertNotIn("C:\\lark-cli", text)
        self.assertNotIn("D:\\lark-cli", text)
```

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```powershell
python .\plugins\picturebook-screenwriter\skills\feishu-knowledge-store\scripts\test_config.py
python .\plugins\picturebook-screenwriter\skills\feishu-knowledge-store\scripts\test_lark_cli_bootstrap.py
```

Expected: FAIL while `C:\lark-cli` and `D:\lark-cli` remain.

- [ ] **Step 3: Remove author-specific fallbacks**

In `config.py`, remove:

```python
_WINDOWS_CLI_FALLBACK = Path(r"D:\lark-cli\lark-cli.exe")
```

and remove that path from candidate construction. Keep:

```python
for candidate in (environment.get("LARK_CLI_PATH"), which("lark-cli")):
```

In `lark_cli_bootstrap.py`, update `_platform_candidates()`:

```python
def _platform_candidates(environment):
    if os.name == "nt":
        candidates = []
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
```

- [ ] **Step 4: Run focused tests**

Run:

```powershell
python .\plugins\picturebook-screenwriter\skills\feishu-knowledge-store\scripts\test_config.py
python .\plugins\picturebook-screenwriter\skills\feishu-knowledge-store\scripts\test_lark_cli_bootstrap.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/config.py `
  plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/lark_cli_bootstrap.py `
  plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_config.py `
  plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_lark_cli_bootstrap.py
git commit -m "fix(feishu): use portable cli discovery"
```

## Task 10: Portable Documentation and 0.3.2 Release

**Files:**
- Modify: `plugins/picturebook-screenwriter/README.md`
- Modify: `plugins/picturebook-screenwriter/skills/knowledge-loader/SKILL.md`
- Modify: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/SKILL.md`
- Modify: `plugins/picturebook-screenwriter/.codex-plugin/plugin.json`
- Modify: `plugins/picturebook-screenwriter/tests/test_release_contract.py`
- Modify: `docs/CHANGELOG.md`

**Interfaces:**
- Consumes: v2 discovery and CLI contracts.
- Produces: generic documentation and plugin version `0.3.2`.

- [ ] **Step 1: Add failing release assertions**

Update version assertions from `0.3.1` to `0.3.2`. Add:

```python
    def test_readme_documents_portable_config_discovery(self):
        text = PLUGIN_README.read_text(encoding="utf-8")
        for value in (
            "ancestor",
            "%APPDATA%\\picturebook-screenwriter",
            "${XDG_CONFIG_HOME:-~/.config}/picturebook-screenwriter",
            "PICTUREBOOK_KB_CONFIG",
            "deprecated",
        ):
            with self.subTest(value=value):
                self.assertIn(value, text)

    def test_readme_has_no_author_machine_paths(self):
        text = PLUGIN_README.read_text(encoding="utf-8")
        self.assertNotIn("E:\\海外绘本", text)
        self.assertNotIn("C:\\Users\\lvan", text)
```

- [ ] **Step 2: Run release tests and verify they fail**

Run:

```powershell
python .\plugins\picturebook-screenwriter\tests\test_release_contract.py
```

Expected: FAIL because docs and version are not updated.

- [ ] **Step 3: Update documentation and version**

Set:

```json
  "version": "0.3.2",
```

Update README's Configuration discovery section to document:

```text
1. --config
2. PICTUREBOOK_KB_CONFIG
3. nearest workspace ancestor
4. Windows: %APPDATA%\picturebook-screenwriter
   macOS: ~/Library/Application Support/picturebook-screenwriter
   Linux: ${XDG_CONFIG_HOME:-~/.config}/picturebook-screenwriter
5. deprecated: ~/.picturebook-screenwriter
```

Use `<workspace>`, `<project-id>`, `<series-id>`, `<page-types>`, and `<local-plugin-root>` in command examples. Document that each user creates their own local schema-v2 config and that example files are never discovered.

Update SKILL files to state ancestor discovery and portable user fallbacks. Update the changelog:

```markdown
- Portable Feishu config discovery for project subdirectories, user directories, and shared plugin distribution.
```

- [ ] **Step 4: Run release tests**

Run:

```powershell
python .\plugins\picturebook-screenwriter\tests\test_release_contract.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add plugins/picturebook-screenwriter/README.md `
  plugins/picturebook-screenwriter/skills/knowledge-loader/SKILL.md `
  plugins/picturebook-screenwriter/skills/feishu-knowledge-store/SKILL.md `
  plugins/picturebook-screenwriter/.codex-plugin/plugin.json `
  plugins/picturebook-screenwriter/tests/test_release_contract.py `
  docs/CHANGELOG.md
git commit -m "docs: release portable feishu discovery"
```

## Task 11: Portable Offline Gates and Manual Acceptance

**Files:**
- No repository code changes in the automated gate.
- Create outside the repository: `<selected-config-owner>/feishu-knowledge-base.json`

**Interfaces:**
- Consumes: plugin version `0.3.2`.
- Produces: automated validation and user-local acceptance evidence.

- [ ] **Step 1: Run automated offline gates**

Run:

```powershell
python -m unittest discover -s .\plugins\picturebook-screenwriter\skills\feishu-knowledge-store\scripts -p "test_*.py"
python -m unittest discover -s .\plugins\picturebook-screenwriter\skills\knowledge-loader\scripts -p "test_*.py"
python .\plugins\picturebook-screenwriter\tests\test_release_contract.py
python <codex-home>\skills\.system\plugin-creator\scripts\validate_plugin.py .\plugins\picturebook-screenwriter
python .\scripts\governance_check.py
python .\scripts\package_check.py
git diff --check
```

Expected: all commands exit `0`.

- [ ] **Step 2: Verify no machine-specific defaults**

Run:

```powershell
git grep -n "E:\\海外绘本" -- plugins
git grep -n "C:\\Users\\lvan" -- plugins
git grep -n "D:\\lark-cli" -- plugins
```

Expected: all searches exit `1` with no matches. The only acceptable placeholder remains `REPLACE_WITH_TARGET_ROOT_TOKEN` in the example and tests.

- [ ] **Step 3: Test shared workspace inheritance**

With a real schema-v2 config at `<series-workspace>/feishu-knowledge-base.json`, run from a project subdirectory:

```powershell
python <plugin-root>\skills\feishu-knowledge-store\scripts\store_cli.py config-status --workspace <series-workspace>\<project>
```

Expected JSON: `configured: true`, `origin: "ancestor"`, and a path pointing to `<series-workspace>/feishu-knowledge-base.json`.

- [ ] **Step 4: Test user fallback**

Temporarily remove or rename the workspace-chain config, create the platform-standard user config, then rerun `config-status`.

Expected JSON: `configured: true`, `origin: "user"`.

Restore the workspace-chain config after the test.

- [ ] **Step 5: Run read-only authority load**

Run:

```powershell
python <plugin-root>\skills\knowledge-loader\scripts\authority_cli.py load --workspace <series-workspace>\<project> --project-id <project-id> --series-id <series-id> --page-types worldview,characters,content_spec
```

Expected: `offline: false`, required page keys present, and each item contains `revision_id` and `source_revisions`.

- [ ] **Step 6: Record result**

Record command names, origins, statuses, and page keys. Do not record the target root token or the full evidence body.
