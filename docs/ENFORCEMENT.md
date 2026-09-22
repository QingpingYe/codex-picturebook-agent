# Enforcement Levels

`plugin-contract.json` is a public plugin boundary and validation input. It is
not loaded by the runtime execution path and does not act as a sandbox.

The levels below describe where a guarantee actually lives.

- `runtime_required`: execution rejects or blocks an invalid operation.
- `script_checked`: a script validates required inputs, but cannot prove user
  intent or prevent an authorized caller from supplying valid values.
- `prompt_only`: behavior depends on the model following Skill instructions;
  there is no host-level prevention.

A `runtime_required` level only applies when its owner script is in the execution path.
Whether a Skill chooses to invoke that script remains prompt-driven unless
host policy enforces the call.

## Matrix

| Gate | Level | Enforcement point | Consequence |
|---|---|---|---|
| Stage DAG schema, transitions, and terminal state | `runtime_required` | `plugins/picturebook-screenwriter/scripts/stage_dag.py` | Invalid manifests and transitions fail closed. |
| Lead-owned confirmation gates are not dispatched | `runtime_required` | `plugins/picturebook-screenwriter/scripts/stage_dag_codex.py` | Dispatch planning waits instead of assigning a lead-owned gate. |
| Agent output path scope | `runtime_required` | `plugins/picturebook-screenwriter/scripts/stage_dag.py` | Traversal and paths outside the agent scope are rejected. |
| Feishu revision-protected writes | `runtime_required` | `skills/feishu-knowledge-store/scripts/publisher.py` | Writes use the observed revision and verify readback. |
| Self-contained HTML image embedding and size limit | `runtime_required` | `skills/illustration-export/scripts/embed_images.py` | Missing images or over-limit output fail with a non-zero result. |
| Image output directory, size, quality, and API key source | `script_checked` | `skills/image-generate/generate.js` | Missing arguments or key sources fail before generation. |
| HTML build and session export output arguments | `script_checked` | `skills/illustration-export/scripts/build_html.py`, `skills/session-export/scripts/export_session.py` | Callers must provide the expected inputs before the scripts write files. |
| Explicit user confirmation before image generation | `prompt_only` | Entry and `image-generate` Skill instructions | A caller that invokes the script directly is not blocked by a consent token. |
| Confirmation gate and default dialog-only write policy | `prompt_only` | Entry Skill instructions | Scripts cannot prove that the user approved a write. |

Use host-level permissions, approval prompts, or hooks when a guarantee must
hold even if a model ignores the Skill instructions.
