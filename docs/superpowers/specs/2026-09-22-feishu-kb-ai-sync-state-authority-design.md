# Feishu KB-AI Sync State Authority Design

**Status:** Approved design, pending implementation plan  
**Date:** 2026-09-22  
**Scope:** `picturebook-screenwriter` Feishu knowledge sync, publishing, and retrieval boundaries

## 1. Problem

The Feishu KB-AI sync protocol already says that remote state is authoritative, but the
runtime still has paths that can treat task-local files as if they were the state of the
target Wiki. In particular, `sync_runner.py` can publish every manifest entry without first
reading and classifying against the remote sync index.

This creates an ambiguity between three different kinds of state:

1. Target publication state in the Feishu KB-AI.
2. Source-side delta state used for the current extraction run.
3. Local run reports and candidate artifacts.

Only the first kind is shared sync state. The other two are task-local evidence and must not
be used to reconstruct, override, or replace target state.

## 2. Goal

The sole authoritative file for Feishu KB-AI sync state is the remote control document:

```text
99_系统控制台/AI_KB_INDEX_V1
```

All decisions about whether a logical key is published, needs review, archived, absent, or
safe to update must start from a validated read of this document. Target page metadata is
used for cross-checking, but a successful target sync state change is not complete until
the remote index has also been conditionally updated and read back.

## 3. State Boundaries

| Artifact | Authority | Allowed use |
| --- | --- | --- |
| `AI_KB_INDEX_V1` | Sole sync-state authority | Defines logical-key status, `doc_token`, `wiki_node_token`, `source_revisions`, `last_ai_revision_id`, and `last_seen_revision_id` |
| Target page footer metadata | Page consistency evidence | Must agree with the index entry; disagreement makes the page `needs_review` |
| `AI_KB_LOCK_V1` | Remote concurrency control | Determines lock ownership; does not define page publication status |
| `AI_KB_CONFLICT_QUEUE_V1` | Remote conflict event log | Records unresolved conflicts; does not replace or mutate index status |
| `nodes_snapshot.json` | Current-run source fact | Describes the source tree observed in this run |
| `delta_state.json` | Current-run source delta state | May decide whether source content is re-read; never defines target page status |
| `_manifest.json` | Current-run candidate proposal | Proposes content for target synchronization; is not a state baseline |
| `sync_report.json` | Local run report | Describes what this run attempted; never seeds the next run |
| Knowledge loader cache | Non-authoritative offline cache | May be used only after explicit user approval and must remain marked non-authoritative |

`first_run`, `new`, `changed`, `unchanged`, `deleted`, and `unknown` are source-delta
verdicts. They are not target status values and must never appear in the index `status`
field. The only valid target status values remain `published`, `needs_review`, and
`archived`.

## 4. Source Ingestion Behavior

Source ingestion remains direct and does not require the user to confirm new source nodes.

The source Wiki is read-only input. The ingestion flow may enumerate every source node and
classify nodes as `first_run`, `new`, `changed`, `unchanged`, `deleted`, or `unknown`.
Nodes with verdicts `first_run`, `new`, `changed`, and `unknown` proceed directly into
reading, extraction, candidate synthesis, and mechanical validation.

This behavior is intentional. A source-side `new` node only means that this run has not
seen that source node before. It does not imply a target-side confirmation decision, and it
must not block candidate generation.

## 5. Required Target Sync Flow

1. Complete source extraction and generate the task-local `_manifest.json`.
2. Acquire the remote lease in `AI_KB_LOCK_V1`.
3. Read and validate `AI_KB_INDEX_V1`.
4. Classify every manifest candidate against the remote index and the current target page.
5. Perform page writes only after the target action is classified.
6. Read back the target page after every write.
7. Update `AI_KB_INDEX_V1` conditionally and read it back.
8. Report counts and affected logical keys.
9. Release the remote lease.

A candidate whose logical key is absent from the remote index is a first-publication
candidate. It may be created without a user inclusion decision. If a target page with the
same logical key already exists, the run must not silently adopt it; the affected key must
be reported as needing review.

For an existing index entry, the index's `source_revisions` is the source-version baseline.
The candidate, the remote page, and the index entry are evaluated together before any
merge, preserve, or conflict decision.

## 6. Failure Semantics

| Failure | Required behavior |
| --- | --- |
| Index missing during normal sync | Stop all target writes; do not create a replacement from local data |
| Index malformed, duplicated, or schema-invalid | Stop all target writes; report the validation error |
| Remote page missing for an indexed key | Treat the affected key as `needs_review`; do not recreate automatically |
| Page exists but is absent from the index | Do not silently adopt the page; require review |
| Page write fails | Do not report `published`; retain the previous remote state as far as it is verifiable |
| Page write succeeds but index update fails | Do not report complete success; record or report the key as needing review |
| Target Wiki unavailable | Sync fails; no local file may serve as a status fallback |
| Source node deleted | Do not automatically archive the target page |

If the index cannot be updated, the implementation must not fabricate a new index status
locally. It may append or report a conflict and must ensure the next run revalidates both
the remote page and the remote index.

## 7. Runtime Boundary Changes

### `ControlPlane`

`ControlPlane` is the only component allowed to read and write the remote sync index. It
must continue to enforce strict schema validation, revision-based conditional updates, and
read-after-write comparison.

### `SyncRunner`

`SyncRunner.publish()` must stop treating every manifest entry as a first publication. It
must first obtain and validate remote state through `ControlPlane.read_index()`, then route
each candidate through the target-action classifier.

### `Publisher`

`Publisher` performs target page creation, conditional update, metadata correction, and
page readback. It returns the resulting index entry for the caller to persist through
`ControlPlane`. It must not maintain a private target-state cache.

### `wiki-ingest`

`wiki-ingest` continues to generate candidate proposals only. It must not write target
state and must not gate `first_run` or `new` source nodes behind a user inclusion prompt.

## 8. Compatibility Documentation Update

The WorkBuddy compatibility contract should make the boundary explicit:

1. `AI_KB_INDEX_V1` is the only shared sync-state file.
2. Local extraction and staging files are not cross-run sync state.
3. Target state recovery always starts from the remote index and remote pages.
4. A local run report or candidate manifest cannot reconstruct a missing or corrupt index.

## 9. Testing Requirements

Add or update tests to verify:

1. A corrupt or missing remote index blocks target writes during normal sync.
2. Local `delta_state.json`, `_manifest.json`, `source_nodes.json`, and `sync_report.json`
   cannot replace or reconstruct remote index state.
3. A candidate absent from the remote index goes through first publication.
4. A target page that exists without an index entry becomes a review case.
5. A page write is not reported as successful unless the index update and readback also
   succeed.
6. Source `first_run` and `new` verdicts proceed into candidate generation without a user
   confirmation gate.
7. `first_run`, `new`, `changed`, `unchanged`, `deleted`, and `unknown` are rejected if
   proposed as target index status values.

## 10. Non-Goals

1. Do not add a user confirmation gate for new source nodes.
2. Do not add a source-node inclusion ledger to the index schema.
3. Do not use a local database or durable local file as the target sync baseline.
4. Do not automatically archive target pages when source nodes disappear.
5. Do not introduce `source_edit_times` or another index-field migration in this design.
