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
| Missing `TYPESAFE_API_KEY` means no request is constructed | `runtime_required` | `skills/jev-decision-runtime/scripts/jev_client.py` | A missing, empty, or blank key returns `waiting_for_jev_key` and the transport is never called. |
| Jev endpoint allowlist and disabled redirects | `runtime_required` | `skills/jev-decision-runtime/scripts/jev_client.py` | Any URL other than `https://api.typesafe.ai/v1/systemone` is refused before the socket opens, and 3xx responses are never followed. |
| Single-holder execution lease per operation | `runtime_required` | `skills/jev-decision-runtime/scripts/jev_runner.py` | A second acquire while an unexpired lease exists raises `LeaseHeld`. This is a same-host local-file lease and **不提供跨主机互斥**. |
| Terminal result lands before the pending call is cleared | `runtime_required` | `skills/jev-decision-runtime/scripts/jev_runner.py` | `result.json` is written before `pending.json` is removed, and a resume that finds a terminal result finishes the bookkeeping instead of dispatching again. |
| An ambiguous outcome is never resent automatically | `runtime_required` | `skills/jev-decision-runtime/scripts/jev_runner.py` | A request that may have reached the service is recorded as `outcome_unknown`; resume reports that state again and refuses to dispatch without explicit user consent. |
| The response model must equal the policy's pinned model | `runtime_required` | `skills/jev-decision-runtime/scripts/jev_runner.py` | A response whose `model` differs from `pinned_model` is recorded as `model_version_mismatch` and never produces a succeeded result. |
| Only `request.json` carries the state body on disk | `runtime_required` | `skills/jev-decision-runtime/scripts/jev_runner.py` | `pending.json`, `decision-context.json`, the trace, and the result carry no input text; `request.json` is the one file the resume path needs, and it carries no credential. |
| Credential arguments are refused on the command line | `runtime_required` | `skills/jev-decision-runtime/scripts/jev_runner.py` | Any `--api-key` / `--token` / `--secret` style flag is rejected before the parser runs, and the value is never echoed. |
| Pending call fingerprint and revision freshness | `script_checked` | `skills/jev-decision-runtime/scripts/jev_runner.py` | Resume compares the fingerprint and revision vector first and reports `superseded` on a mismatch, but a caller can still supply matching inputs. |
| Jev request, result, and trace schema | `script_checked` | `skills/jev-decision-runtime/scripts/decision_contract.py` | Invalid requests, invalid results, and missing answer ids raise `ContractError`. |
| Cost and token accounting | `script_checked` | `skills/jev-decision-runtime/scripts/telemetry.py` | Cost stays decimal and fixed-point; missing usage yields null tokens and null cost instead of a character-count guess. |
| The first response asks for the execution choice | `prompt_only` | Entry Skill instructions | Scripts cannot prove the question really came first. |
| A missing key waits instead of falling back to the plain LLM | `prompt_only` | Entry and `jev-decision-runtime` instructions | A caller that invokes the runner directly is not blocked by a consent token. |
| Jev is only called after the user chooses it | `prompt_only` | Entry Skill instructions | There is no host-level prevention. |
| 硬约束块不可被过滤 | `runtime_required` | `skills/knowledge-loader/scripts/required_marking.py` | 未发布来源、索引未同步、未知页型、无标题前言与全部 `machine-data` 约束块一律标记 required。 |
| `exclude_soft` 只作用于召回命中的软块 | `runtime_required` | `skills/knowledge-loader/scripts/recall.py` | 未召回的软块无条件保留，永不进入 Jev。 |
| 锁 bundle 保留完整版本向量 | `runtime_required` | `skills/knowledge-loader/scripts/relevance.py` | `dependency_bundle()` 返回未精简的证据包；精简只作用于模型上下文。 |
| 路由 band 与规则首命中 | `script_checked` | `skills/jev-decision-runtime/scripts/routing.py` | 规则与阈值来自策略文件；脚本不能证明阈值已按中文样本校准。 |
| 相关性问题模板与规则 | `prompt_only` | `references/decision-policies.json` + 技能指令 | 模板文本由策略提供，是否调用甄别仍取决于技能指令。 |

Use host-level permissions, approval prompts, or hooks when a guarantee must
hold even if a model ignores the Skill instructions.
