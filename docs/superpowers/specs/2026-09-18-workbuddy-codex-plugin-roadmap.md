# WorkBuddy to Codex Plugin Roadmap

**Date:** 2026-09-18  
**Status:** Accepted high-level roadmap  
**Source project:** `E:\picturebook-screenwriter\workbuddy-expert`  
**Target plugin:** `E:\codex-picturebook-agent\plugins\picturebook-screenwriter`

## Goal

Port the WorkBuddy picture-book expert team into a Codex plugin while preserving its editorial workflow, craft methodology, authoritative Feishu knowledge model, quality gates, illustration pipeline, and governance. The Codex version is plugin-native: it must run with ordinary Codex skills and scripts, avoid WorkBuddy-only runtime assumptions, and keep every write behind an explicit user decision.

## Phase Order

1. **Plugin contract** — define the plugin's public boundary, skill graph, workflow states, permissions, unsupported capabilities, and release acceptance criteria.
2. **Knowledge loop** — make Feishu knowledge an active creation dependency through authority loading, `built_against` locking, collision checks, stale propagation, and ingestion reminders.
3. **Creative pipeline** — port lightweight planning and the five baseline slots so the plugin can run a complete editorial workflow without WorkBuddy subagents.
4. **Quality gates** — port and adapt cheap proxies, semantic review, project red lines, wiki lint, and Lexile checks into deterministic, auditable gates.
5. **Art and export** — connect staging, prompt architecture, image generation, asset preproduction, and self-contained HTML preview.
6. **Governance and release** — add session export, structural self-checks, packaging, versioning, marketplace publication, and multi-user acceptance.

## Cross-Phase Constraints

- The original Feishu Wiki is read-only input.
- The target Feishu Wiki is the only shared authority.
- Local caches and run directories are never shared authority.
- Human edits win over AI-generated content.
- User-visible text is Chinese.
- Files are written only after explicit approval or an explicit export request.
- No WorkBuddy-only APIs, agents, hooks, or filesystem assumptions may leak into plugin runtime code.
- Every stage must keep offline unit tests passing; live Feishu and image API checks are separate acceptance steps.

## Phase Acceptance

| Phase | Working software delivered | Primary proof |
| --- | --- | --- |
| 1 | A contract-tested plugin boundary | Skill graph, workflow, permissions, and unsupported-capability tests |
| 2 | Knowledge-dependent creation | Evidence bundles, `built_against`, collision reports, stale propagation tests |
| 3 | Complete editorial workflow | Six artifact types, lightweight mode, and five baseline-slot contracts |
| 4 | Enforced quality workflow | Deterministic reports, red-line gates, semantic review records, and Lexile adapter |
| 5 | Illustration and preview flow | Prompt payloads, image metadata, self-contained HTML, and visual regression fixtures |
| 6 | Distributable plugin | Packaging validation, governance report, release checklist, and multi-user smoke test |

## Plan Index

1. `docs/superpowers/plans/2026-09-18-01-plugin-contract.md`
2. `docs/superpowers/plans/2026-09-18-02-knowledge-loop.md`
3. `docs/superpowers/plans/2026-09-18-03-creative-pipeline.md`
4. `docs/superpowers/plans/2026-09-18-04-quality-gates.md`
5. `docs/superpowers/plans/2026-09-18-05-art-export.md`
6. `docs/superpowers/plans/2026-09-18-06-governance-release.md`

## Non-Goals

- Reimplementing WorkBuddy's TeamCreate, SendMessage, hooks, or subagent runtime.
- Treating the WorkBuddy local library as an authority.
- Automatically editing the original Feishu Wiki.
- Silently generating images, writing files, or spending API credits.
- Making the plugin depend on a machine-specific `D:\lark-cli` installation.
