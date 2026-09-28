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
| Hard-constraint blocks cannot be filtered | `runtime_required` | `skills/knowledge-loader/scripts/required_marking.py` | Unpublished sources, an unsynced index, an unknown page type, an untitled preamble, and every `machine-data` block are marked required. |
| `exclude_soft` only reaches a recalled soft block | `runtime_required` | `skills/knowledge-loader/scripts/recall.py` | A soft block that recall did not select is kept unconditionally and never reaches Jev. |
| The lock bundle keeps the whole revision vector | `runtime_required` | `skills/knowledge-loader/scripts/relevance.py` | `dependency_bundle()` returns the unreduced evidence bundle; reduction applies to the model context only. |
| The screening path holds the same per-operation execution lease | `runtime_required` | `skills/knowledge-loader/scripts/relevance.py` | Every batch is dispatched through `jev_runner.run_operation`, so a batch whose lease another runner holds raises `LeaseHeld` and is never sent. |
| A screen never leaves more than one call to continue | `runtime_required` | `skills/knowledge-loader/scripts/relevance.py` | A batch that settled as a failure drops its pending record under the operation lease and is retried by re-running the screen; only a call that waits for the credential or whose fate is unknown keeps a `pending.json`, so `resume` stays reachable for a multi-batch bundle. |
| A screen never re-sends a record it cannot read | `runtime_required` | `skills/knowledge-loader/scripts/relevance.py` | A request, result, or pending record that exists but does not parse — or parses to something other than an object — a result that does not satisfy the contract for its batch, and a pending record whose `attempt_status` is not one of the two (`pending`, `failed`) that leave nothing open behind them, all keep their batch out of the screen instead of paying for it again. |
| A stored result is reused only for the request it answered | `runtime_required` | `skills/jev-decision-runtime/scripts/jev_runner.py` | `result_answers_request` compares the result's own `trace.input_sha256` with the request in hand, so a record written before an edit — or one that never recorded which request it answered — is never routed from, by `resume` or by the screening path. |
| A screen that stops on a record it cannot vouch for names it | `runtime_required` | `skills/knowledge-loader/scripts/relevance.py` | Every record that keeps a batch out of the screen is reported as `blocked_records` (operation id, path, and reason), so a stopped run is actionable instead of stopping silently in the same place on every re-run. |
| A settled operation clears only its own pending call | `runtime_required` | `skills/jev-decision-runtime/scripts/jev_runner.py` | The run-level `pending_call` in `decision-context.json` is cleared only when no other operation is still waiting; when a second operation's record is on disk the field keeps naming it, so a copied manifest is never told there is no call to continue. |
| Routing bands and first-match rule order | `script_checked` | `skills/jev-decision-runtime/scripts/routing.py` | Rules and thresholds come from the policy file; the script cannot prove the thresholds were calibrated on Chinese samples. |
| Relevance question templates and routing rules | `prompt_only` | `references/decision-policies.json` plus the Skill instructions | The policy supplies the template text, and whether screening runs at all still depends on the Skill instructions. |
| An authority violation cannot be downgraded | `runtime_required` | `skills/pre_output-baseline/scripts/quality_gate.py` | `apply_judgments` never changes a `project_authority` finding's severity, and every authority finding blocks whatever severity it stores; a proxy candidate is promoted to `project_authority` by `promote_confirmed_redlines`, the only path that produces a blocking finding. |
| A failure never produces a clear verdict | `runtime_required` | `skills/pre_output-baseline/scripts/screening.py` | No answer, an answer no rule can band, and a routing error all become `runtime_failure`, which carries no probabilities and can never skip the plain-LLM review. |
| An `experimental` operation never reduces the review | `runtime_required` | `skills/pre_output-baseline/scripts/screening.py` | `may_skip_llm_review()` requires a `screened_clear` decision and `calibration_status == "calibrated"`, so while an operation is experimental a clear verdict only produces comparison data. |
| Every active red line is asked about every page window that carries text | `script_checked` | `skills/pre_output-baseline/scripts/screening_runner.py` | Dimensions come from the catalog rather than from the regex hits, so a red line the literal scan missed is still asked; a wordless spread produces no item by design (there is nothing to judge), and whether the catalog itself is complete still depends on the authority knowledge. |
| The red-line catalog is rebuilt from authority pages | `script_checked` | `skills/pre_output-baseline/scripts/redline_catalog.py` | A `machine-data: redline_terms` or `banned_terms` block on a constraint page is preferred over the quoted fragments of its protected sections, and an empty catalog is reported as a gap instead of as "no red lines". |
| The pre-screen reuses a settled batch and never re-sends a possibly billed one | `runtime_required` | `skills/pre_output-baseline/scripts/screening_runner.py` | A batch whose stored request matches is routed from its settled result instead of being paid for again; an open attempt, or a record that cannot be read as this request's verdict, keeps the batch out of the screen, becomes that batch's `runtime_failure`, and is named in `blocked_records`. |
| The pre-screen entry point refuses a draft it cannot read | `runtime_required` | `skills/pre_output-baseline/scripts/screening_cli.py` | A file whose page table parses to no row and an authority bundle that cannot be read as one both exit with a JSON error before any request is sent; a draft whose pages are all wordless is reported as `item_count = 0` with its own warning instead of as a pass. |
| The pre-screen CLI reports its coverage instead of a bare exit code | `script_checked` | `skills/pre_output-baseline/scripts/screening_cli.py` | The report carries `catalog_gap`, `may_skip_llm_review`, the `summary` ratios, `blocked_records` and `warnings`, and the command still exits 0 when every dimension waited for the credential or failed — so the verdict lives in the report, and whether the pre-screen should run at all remains prompt-driven. |
| Threshold calibration status | `prompt_only` | `skills/pre_output-baseline/references/calibration-samples.md` plus the Skill instructions | The scripts cannot prove the thresholds were calibrated on Chinese samples; only a human-accepted measurement moves an operation from `experimental` to `calibrated`. |

Use host-level permissions, approval prompts, or hooks when a guarantee must
hold even if a model ignores the Skill instructions.
