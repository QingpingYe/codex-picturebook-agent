# Feishu Knowledge Config Discovery Design

Date: 2026-09-21  
Status: Approved design, pending implementation plan  
Scope: Picture Book Screenwriter Feishu knowledge configuration discovery, read-only knowledge loading, and operational diagnostics

## 1. Problem

The Feishu sync path succeeded on 2026-09-17, but the successful run did not leave a durable, discoverable knowledge-base configuration. The runtime configuration was passed explicitly to a plugin-cache example file. Later plugin upgrades removed that cache version, while the current workspace retained neither a real `feishu-knowledge-base.json` nor the `PICTUREBOOK_KB_CONFIG` environment variable.

On 2026-09-21, a lightweight story-planning request correctly entered `knowledge-loader`. The Lark CLI bootstrap succeeded, proving that CLI availability and supported versions were not the issue. The loader could not continue because no valid configuration path could be found. No last-confirmed local evidence cache existed, so the workflow fell back to local project files and explicitly marked the output as not authority-verified.

The implementation also has a discovery defect: `store_cli.build_components()` derives its default workspace from the script location with `Path(__file__).resolve().parents[5]`. In an installed plugin this resolves to the plugin cache root, not the user workspace. Cache directories are ephemeral and are the wrong owner for durable workspace configuration.

## 2. Goals

1. Make a valid Feishu knowledge configuration discoverable across new sessions and plugin upgrades.
2. Keep workspace-local configuration as the primary durable source.
3. Preserve `PICTUREBOOK_KB_CONFIG` as a portable override.
4. Keep plugin cache directories usable for code and examples only, never as durable configuration.
5. Provide one read-only CLI path for authority retrieval so skills do not manually wire internal components.
6. Support an explicit, non-authoritative offline fallback only when the caller opts in.
7. Report configuration problems with enough detail to repair them without exposing secrets.

## 3. Non-Goals

1. Do not change the Feishu source/target synchronization algorithm.
2. Do not introduce a centralized configuration service, remote secret store, or cloud profile system.
3. Do not make the example configuration discoverable.
4. Do not implicitly use the offline cache as authority.
5. Do not store tokens in the repository, documentation, logs, or plugin cache.

## 4. Configuration Contract

The schema remains version 2. The file may live in the workspace root as:

```text
<workspace>/feishu-knowledge-base.json
```

The user-level fallback may live as:

```text
~/.picturebook-screenwriter/feishu-knowledge-base.json
```

`config/feishu-knowledge-base.example.json` remains a packaging-time template only. It must never be returned by config discovery.

Resolution uses this priority:

1. Explicit `--config`.
2. `PICTUREBOOK_KB_CONFIG`.
3. `<workspace>/feishu-knowledge-base.json`.
4. `~/.picturebook-screenwriter/feishu-knowledge-base.json`.

Explicit command-line configuration takes precedence over the environment variable. This differs from the current loader behavior and is intentional: a caller that names a specific path should get that exact file. The environment variable remains useful for users who prefer not to place the file in a workspace.

Workspace resolution uses, in order:

1. An explicit `--workspace` argument.
2. The current working directory.

The plugin script location must not be used to infer the workspace.

## 5. Runtime Design

### 5.1 Shared resolver

Add one resolver used by all CLI and programmatic entry points:

```python
resolve_config_path(
    explicit_path: Path | None,
    workspace: Path | None,
    environ: Mapping[str, str] | None,
) -> ResolvedConfigPath
```

`ResolvedConfigPath` contains:

- `path`
- `origin`: one of `explicit`, `environment`, `workspace`, `user`
- `searched`: ordered list of candidate paths checked before resolution or failure

`load_config()` continues to validate JSON schema v2. It accepts an already resolved path. It rejects placeholder `target.root_token` values with an explicit error.

### 5.2 Component construction

Change:

```python
build_components(config_path=None, workspace=None, environ=None)
```

It resolves the configuration before constructing `LarkCli`, `Publisher`, and `ControlPlane`. It must not derive workspace from `__file__`.

### 5.3 CLI compatibility

`store_cli` and `sync_runner` accept:

- `--config`: optional
- `--workspace`: optional

Existing commands that pass `--config` remain compatible. When `--config` is omitted, the shared resolver chooses the path.

### 5.4 Config status

Add `store_cli config-status`. It is read-only and emits JSON without token values:

```json
{
  "configured": false,
  "status": "missing_config",
  "origin": null,
  "path": null,
  "searched": [
    "E:\\海外绘本\\feishu-knowledge-base.json",
    "C:\\Users\\<user>\\.picturebook-screenwriter\\feishu-knowledge-base.json"
  ],
  "cli_status": {
    "status": "available",
    "path": "D:\\lark-cli\\lark-cli.exe",
    "version": "1.0.95"
  }
}
```

Possible configuration statuses are:

- `configured`
- `missing_config`
- `invalid_config`
- `placeholder_target_token`

The command may validate CLI availability and version, but it must not perform Feishu authentication or remote writes.

## 6. Authority Loader CLI

Add a read-only command under `knowledge-loader/scripts`:

```powershell
python .\skills\knowledge-loader\scripts\authority_cli.py load `
  --project-id "小老鼠迈尔斯" `
  --series-id "海外绘本" `
  --page-types worldview,characters,content-spec `
  --workspace "E:\海外绘本"
```

The command also accepts optional `--config`. It resolves configuration, constructs components, and calls the existing authority-loading contracts. Output is JSON containing items, warnings, `offline`, and `fetched_at`.

Every successful item includes:

- `key`
- `doc_token`
- `revision_id`
- `source_revisions`

The command never writes project artifacts. It only updates the last-confirmed evidence cache after a successful remote read.

## 7. Offline Cache

After a successful remote read, the authority CLI writes:

```text
<workspace>/.picturebook-screenwriter/cache/<safe-user>/knowledge-bundle.json
```

`<safe-user>` is a local filesystem-safe cache namespace, not a permission identity. It may be derived from the local username or default to `local-user`.

Remote failure can return cached evidence only when the command is called with:

```text
--allow-offline-cache
```

Offline output must set `offline: true` and include a warning stating that the cache is not authority and is only the last confirmed snapshot. If no cache exists, the command fails with the remote error and cache status.

## 8. User-Facing Errors

Missing or invalid configuration must produce a machine-readable error that includes:

- `status`
- all searched paths
- the resolution origin, when available
- a Chinese human-readable hint

The error must distinguish:

1. No configuration found.
2. Configuration file unreadable or invalid JSON.
3. Schema version or required field invalid.
4. Placeholder target token.
5. CLI missing or unsupported.

The message must not print the target root token or other credential-shaped values.

## 9. Documentation Contract

README, `knowledge-loader`, and `feishu-knowledge-store` documentation must state:

1. The recommended configuration location is the workspace root.
2. `PICTUREBOOK_KB_CONFIG` overrides workspace discovery after an explicit CLI path.
3. Plugin cache directories are not durable state.
4. Example files are templates, not live configuration.
5. `authority_cli.py` is the standard read-only retrieval entry point.
6. Offline cache is non-authoritative and requires explicit opt-in.

Documentation and tests must not contain real tokens.

## 10. Release and Migration

Implementation happens in the source repository and is released as a plugin version bump, not by editing an installed cache directory.

Operational migration is:

1. Create `<workspace>/feishu-knowledge-base.json` from the schema-v2 example.
2. Replace the placeholder target token with the user-approved real token.
3. Run `store_cli config-status` without setting the environment variable.
4. Run a read-only remote verification.
5. Run `authority_cli.py load` and confirm the evidence cache is created.

After release, the plugin cache may be removed or upgraded without losing workspace configuration.

## 11. Testing

Add focused unit tests for:

1. Resolution order: explicit path, environment variable, workspace file, user file.
2. No discovery of plugin-cache example files.
3. Workspace inference uses `--workspace` or current working directory, not script location.
4. Explicit `--config` compatibility for existing `store_cli` and `sync_runner` commands.
5. `config-status` statuses and secret redaction.
6. Authority CLI successful load, authority gap, remote failure without cache, and explicit offline-cache fallback.
7. Cache write and cache-load round trips.

Run the existing test suites plus the new tests:

```powershell
python -m unittest discover -s .\plugins\picturebook-screenwriter\skills\feishu-knowledge-store\scripts -p "test_*.py"
python -m unittest discover -s .\plugins\picturebook-screenwriter\skills\knowledge-loader\scripts -p "test_*.py"
```

## 12. Acceptance Criteria

1. A new session with a workspace configuration and no environment variable can retrieve authority evidence.
2. Plugin upgrade or cache removal does not delete the configuration.
3. Configuration absence produces an actionable machine-readable error, not a silent local fallback.
4. The offline cache is used only with explicit opt-in and is always labeled non-authoritative.
5. `config-status` never exposes credentials.
6. Existing explicit `--config` usage continues to work.
7. All existing and new unit tests pass.

