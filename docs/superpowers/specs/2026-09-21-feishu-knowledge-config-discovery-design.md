# Feishu Knowledge Config Discovery Design

Date: 2026-09-21  
Revision: v2, portable multi-user discovery
Status: Approved design; implementation plan revision v2 prepared
Scope: Picture Book Screenwriter Feishu knowledge configuration discovery, read-only knowledge loading, operational diagnostics, and portable Lark CLI lookup
Distribution: The plugin is shared with multiple users, machines, projects, and operating systems. No design, example, command, test, or default may depend on a personal filesystem path.

## 1. Problem

The first revision solved the immediate failure on one workspace: the successful Feishu synchronization had left no durable configuration, and the plugin incorrectly inferred its workspace from the installed script location. It added workspace configuration discovery, `config-status`, an authority CLI, and an explicitly opt-in offline cache.

After the first revision, a second failure mode remained. A user working in a project subdirectory or another workspace could not find a configuration stored at a shared series-workspace root. The only unexpectedly discoverable configuration was an old schema-v1 WorkBuddy temporary file. That exposed two portability gaps:

1. Discovery checked only the exact current workspace and one dot-directory user fallback. It did not understand a shared workspace containing multiple project directories.
2. User fallback and several Lark CLI fallbacks were not defined portably enough for a distributed plugin.

The plugin is shared software. Configuration must therefore be defined as a portable ownership and discovery contract, not as a path convention from one author's machine.

## 2. Terminology

- **Workspace** means the directory supplied with `--workspace` or, when omitted, the current working directory. It is a user invocation boundary, never the plugin installation or cache root.
- **Workspace chain** means the workspace directory followed by its parent directories up to the filesystem root.
- **Nearest ancestor** means the first matching configuration encountered while walking that chain from the workspace toward the filesystem root.
- **User configuration directory** means the standard per-user application-configuration directory for the current operating system.
- **Plugin cache** means the read-only package payload installed by Codex or another host. It is never durable configuration storage.

## 3. Goals

1. Make one configuration usable across project subdirectories under a shared workspace.
2. Let independent workspaces keep separate configurations when needed.
3. Use portable, platform-standard user configuration paths.
4. Keep `PICTUREBOOK_KB_CONFIG` as an explicit override for users who do not want configuration in a workspace.
5. Keep plugin cache directories usable for code and examples only, never as durable configuration.
6. Provide one read-only CLI path for authority retrieval so skills do not manually wire internal components.
7. Support an explicit, non-authoritative offline fallback only when the caller opts in.
8. Report configuration provenance and searched paths without exposing credential-shaped values.
9. Work without hardcoded personal directories, drive letters, usernames, or machine-specific CLI paths.

## 4. Non-Goals

1. Do not change the Feishu source/target synchronization algorithm.
2. Do not introduce a centralized configuration service, remote secret store, or cloud profile system.
3. Do not make the example configuration discoverable.
4. Do not implicitly use the offline cache as authority.
5. Do not perform recursive downward scanning of a workspace by default.
6. Do not store tokens in the repository, documentation, logs, or plugin cache.
7. Do not replace multiple team configurations with one machine-specific default.

Recursive downward search may be considered later only as an explicit opt-in with bounded depth, deny-listed directories, and an ambiguity error. It is not part of this revision.

## 5. Configuration Ownership

The schema remains version 2. The canonical file name remains:

```text
feishu-knowledge-base.json
```

Users may choose one of three supported ownership models:

1. **Shared workspace:** place one schema-v2 file at the root of a series workspace. Project subdirectories inherit it through the ancestor chain.
2. **Project workspace:** place a separate schema-v2 file in each project workspace when the project has its own target knowledge base.
3. **User-local:** place one schema-v2 file in the platform-standard user configuration directory, or point `PICTUREBOOK_KB_CONFIG` at any local file.

The plugin package ships only:

```text
config/feishu-knowledge-base.example.json
```

That file is a template. Discovery must never return it. A real configuration belongs to the user or workspace and must not be committed to the plugin repository.

## 6. Resolution Contract

Resolution uses this priority:

1. Explicit `--config`.
2. `PICTUREBOOK_KB_CONFIG`.
3. The workspace chain, nearest directory first.
4. Platform-standard user configuration directories.
5. Deprecated legacy user fallback.

### 6.1 Direct paths

An explicit `--config` path always wins. If `--config` is omitted, `PICTUREBOOK_KB_CONFIG` is the direct path. A direct path must not silently fall back to another candidate when it does not exist or is invalid.

### 6.2 Workspace chain

Starting at the effective workspace, the resolver checks the canonical configuration name in the workspace, then its parent, and continues toward the filesystem root. The nearest match wins. This supports:

```text
<series-workspace>/feishu-knowledge-base.json
<series-workspace>/<project>/...
```

without requiring every project to contain a copy of the configuration.

The plugin script location must never participate in this chain.

### 6.3 Platform user directories

If no workspace-chain file exists, the resolver checks the current user's standard configuration directory:

| Platform | Candidate directory |
|---|---|
| Windows | `%APPDATA%\picturebook-screenwriter` |
| macOS | `~/Library/Application Support/picturebook-screenwriter` |
| Linux | `${XDG_CONFIG_HOME:-~/.config}/picturebook-screenwriter` |

For backward compatibility, the resolver may then check the v1 fallback:

```text
~/.picturebook-screenwriter
```

Documentation must mark this last path deprecated.

### 6.4 Provenance

`origin` must distinguish these values:

- `explicit`
- `environment`
- `workspace`
- `ancestor`
- `user`
- `user-legacy`

`searched` must contain the ordered paths that were considered before success or failure. It must contain paths only, never file contents or credential-shaped values.

## 7. Runtime Design

### 7.1 Shared resolver

All CLI and programmatic entry points use one resolver:

```python
resolve_config_path(
    explicit_path: Path | None,
    workspace: Path | None,
    environ: Mapping[str, str] | None,
    home: Path | None = None,
) -> ResolvedConfigPath
```

`ResolvedConfigPath` contains:

- `path`
- `origin`
- `searched`

The resolver is deterministic and injectable through `workspace`, `environ`, and `home`. Tests must not depend on the author's real user directory.

`load_config()` continues to validate JSON schema v2 and rejects a placeholder `target.root_token` with an explicit error.

### 7.2 Component construction

`build_components()` uses:

```python
build_components(config_path=None, workspace=None, environ=None)
```

It resolves the configuration before constructing `LarkCli`, `Publisher`, and `ControlPlane`. It must not derive workspace from `__file__`.

### 7.3 CLI compatibility

`store_cli` and `sync_runner` accept optional `--config` and `--workspace`. Existing commands that pass `--config` remain compatible.

### 7.4 Config status

`store_cli config-status` is read-only and emits JSON without token values:

```json
{
  "configured": true,
  "status": "configured",
  "origin": "ancestor",
  "path": "<resolved-path>",
  "searched": [
    "<workspace>/feishu-knowledge-base.json",
    "<workspace-parent>/feishu-knowledge-base.json"
  ],
  "cli_status": {
    "status": "available",
    "path": "<resolved-cli-path>",
    "version": "1.0.95"
  }
}
```

Configuration statuses are:

- `configured`
- `missing_config`
- `invalid_config`
- `placeholder_target_token`

The command may validate CLI availability and version, but it must not perform Feishu authentication or remote writes.

## 8. Lark CLI Lookup

The shared plugin must not rely on author-specific drive letters. CLI lookup uses this order:

1. `LARK_CLI_PATH`
2. The executable found on `PATH`
3. Standard npm-global locations derived from the operating system:
   - Windows: `%APPDATA%\npm`
   - Unix: `~/.local/bin`, `/usr/local/bin`, `/opt/homebrew/bin`
4. The installer, only after explicit user approval.

Machine-specific fallbacks such as `C:\lark-cli` or `D:\lark-cli` must be removed from plugin defaults.

## 9. Authority Loader CLI

The standard read-only entry point remains:

```text
<plugin-root>/skills/knowledge-loader/scripts/authority_cli.py load
  --project-id <project-id>
  --series-id <series-id>
  --page-types <comma-separated-page-types>
  --workspace <workspace>
```

It also accepts optional `--config`. It resolves configuration, constructs components, and calls the existing authority-loading contracts. Output is JSON containing items, warnings, `offline`, and `fetched_at`.

Every successful item includes:

- `key`
- `doc_token`
- `revision_id`
- `source_revisions`

The command never writes project artifacts. It updates the last-confirmed evidence cache only after a successful, complete authority read.

## 10. Offline Cache

After a successful remote read, the authority CLI writes:

```text
<workspace>/.picturebook-screenwriter/cache/<safe-user>/knowledge-bundle.json
```

`<safe-user>` is a local filesystem-safe cache namespace, not a permission identity. It may be derived from the local username or default to `local-user`.

Remote failure can return cached evidence only when the command is called with:

```text
--allow-offline-cache
```

Offline output must set `offline: true` and include a warning stating that the cache is non-authoritative and is only the last confirmed snapshot. If no cache exists, the command fails with the remote error and cache status.

An authority gap is not a remote transport failure. It must fail closed even when offline fallback is enabled, and it must not replace an existing complete cache with an incomplete snapshot.

## 11. User-Facing Errors

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

## 12. Documentation Contract

README, `knowledge-loader`, and `feishu-knowledge-store` documentation must use generic placeholders and must state:

1. The recommended configuration location is the relevant workspace root.
2. A project subdirectory inherits the nearest ancestor configuration.
3. `PICTUREBOOK_KB_CONFIG` is used when explicit `--config` is omitted.
4. Platform-standard user directories are the portable fallback.
5. `~/.picturebook-screenwriter` is deprecated.
6. Plugin cache directories are not durable state.
7. Example files are templates, not live configuration.
8. `authority_cli.py` is the standard read-only retrieval entry point.
9. Offline cache is non-authoritative and requires explicit opt-in.
10. Lark CLI should be installed through the user's normal package manager or identified with `LARK_CLI_PATH`.

Documentation examples must not contain author usernames, author drive-letter paths, or real tokens.

## 13. Release and Migration

Implementation happens in the source repository and is released as plugin version `0.3.2`, not by editing an installed cache directory.

Operational migration is:

1. Decide whether the configuration is shared-workspace, project-workspace, or user-local.
2. Create the schema-v2 file from the example at the selected location.
3. Fill in the user-approved target root token locally.
4. Run `store_cli config-status --workspace <workspace>`.
5. Confirm that `origin` and `path` are the intended values.
6. Run a read-only remote verification.
7. Run `authority_cli.py load` and confirm the evidence cache is created.

After release, the plugin cache may be removed or upgraded without losing workspace or user configuration.

## 14. Testing

Add focused unit tests for:

1. Resolution order: explicit path, environment variable, workspace chain, platform user directories, deprecated user fallback.
2. Nearest ancestor wins when multiple ancestor configurations exist.
3. A configuration in a deeper child directory is not discovered by downward recursion.
4. A missing direct `--config` or environment path does not fall back to another candidate.
5. No discovery of plugin-cache example files.
6. Workspace inference uses `--workspace` or the current working directory, not script location.
7. Windows, macOS, and Linux user directories are derived from injected environment and home values.
8. Explicit `--config` compatibility for existing `store_cli` and `sync_runner` commands.
9. `config-status` statuses and secret redaction.
10. Lark CLI lookup has no machine-specific drive-letter fallback.
11. Authority CLI successful load, authority gap, remote failure without cache, and explicit offline-cache fallback.
12. Cache write and cache-load round trips.

Run the existing test suites plus the new tests:

```powershell
python -m unittest discover -s .\plugins\picturebook-screenwriter\skills\feishu-knowledge-store\scripts -p "test_*.py"
python -m unittest discover -s .\plugins\picturebook-screenwriter\skills\knowledge-loader\scripts -p "test_*.py"
```

## 15. Acceptance Criteria

1. A project subdirectory with no local config can discover a schema-v2 config at its shared workspace root.
2. Independent workspaces without local or user configs do not discover each other's configs.
3. A new session with a workspace configuration and no environment variable can retrieve authority evidence.
4. Plugin upgrade or cache removal does not delete the configuration.
5. Configuration absence produces an actionable machine-readable error, not a silent local fallback.
6. The offline cache is used only with explicit opt-in and is always labeled non-authoritative.
7. An authority gap fails closed and does not overwrite an existing cache.
8. `config-status` never exposes credentials and always identifies the selected path's origin.
9. Existing explicit `--config` usage continues to work.
10. No shipped source, test, or documentation path depends on the author's machine.
11. All existing and new unit tests pass.
