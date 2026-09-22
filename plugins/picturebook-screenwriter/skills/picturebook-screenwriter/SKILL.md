---
name: picturebook-screenwriter
description: Picture book screenwriting workshop entry workflow. Use when the user wants to plan, write, revise, or review a children's picture book. It routes requests through an editor-led, role-switched workflow and preserves Chinese output.
---

# Picture Book Screenwriter

把用户请求当作一次绘本编辑部工作流处理。你自己承担四个内部角色：主编、编剧、质检、知识管理。本插件是 Codex 原生实现，不依赖 WorkBuddy 的 TeamCreate、SendMessage 或原生 hooks；当前支持飞书权威知识检索与同步、需要用户明确确认的插画工作流，以及在 Codex 多 Agent 工具可用时的可选阶段 DAG 派发。多 Agent 工具不可用时，使用同一条件决策引擎顺序降级。

## Workflow

1. **Intent**: classify the request as `creation`, `revision`, `review`, `knowledge`, or `illustration`.
2. **Brief gate**: for creation and revision, collect missing essentials before drafting: audience age band, target page count, language, story premise, tone, and any constraints. Ask at most three questions at once.
3. **Knowledge loading**: read `../text-craft/SKILL.md` and the relevant references before drafting. For creation and revision, also invoke `../knowledge-loader/SKILL.md` and use `AuthorityLoader` to retrieve authoritative Feishu knowledge. If the user asks to synchronize the source Feishu Wiki, route the request through `../wiki-ingest/SKILL.md` and then `../feishu-knowledge-store/SKILL.md`.
4. **Stage DAG dispatch** (optional): if the current session has `spawn_agent` / `wait_agent` tools, invoke `../stage-orchestration/SKILL.md` to dispatch the current conditional batch. If those tools are unavailable, use the sequential fallback. Confirmation gates remain lead-owned and are never dispatched to subagents.
5. **Writing**: draft one of the six artifact types, using its dependency token: `positioning`, `topic_plan`, `worldview`, `characters`, `outline`, or `script`.
6. **Pre-output check**: for page-by-page scripts, invoke `../craft-benchmark-check/SKILL.md`.
7. **Quality review**: review the draft against craft principles and the benchmark report. Fix deterministic issues before showing the draft.
8. **确认门**：用中文呈现草稿和基准摘要。确认结果只能是 `approved`、`revision_requested` 或 `cancelled`；在收到结果前保持 `waiting_for_user`，不得派发子 Agent。
9. **Landing**: `approved` 后才允许保存文件，使用版本化 Markdown 文件，例如 `picturebook/positioning_v1.md`。`revision_requested` 不落盘，而是创建新的 revision run。

## Editorial Slots

1. `pre_create-baseline`：复核简报与权威知识缺口。
2. `in_create-baseline`：装载工艺方法、结构骨架和边界卡。
3. `post_create-baseline`：对完整草稿做表达层打磨。
4. `pre_output-baseline`：执行廉价代理扫描和语义判定。
5. `quality-baseline`：补读者视角、朗读测试、跨产物一致性和漏检复查。

所有槽位通过 `scripts/slot_resolver.py` 选择，当前默认使用 baseline 层。未来新增项目层或系列层时，不得改变入口契约。只有用户显式要求轻量模式时，才改用 `../story-planning/SKILL.md`；该技能不落盘，且不得替代完整编辑流程。

## Intent Routes

- `creation`: enter briefing, knowledge loading, drafting, self review, quality review, and confirmation.
- `revision`: enter briefing only for missing constraints, then reuse the same review path.
- `review`: skip drafting and run `craft-benchmark-check` plus the applicable quality review.
- `knowledge`: route to `wiki-ingest` followed by `feishu-knowledge-store`; never write the source Wiki.
- `illustration`: follow the gated illustration route below.

The plugin does not implement WorkBuddy TeamCreate, SendMessage, or native hooks. When Codex multi-agent tools are available, editorial stages may be dispatched through the optional stage DAG; otherwise they run through the sequential fallback.

## Illustration Route

1. 确认脚本已获用户批准，或用户明确提供脚本并要求生成插画。
2. 运行 `../staging-planner/SKILL.md`，得到跨页空间账本和帧向铁律。
3. 运行 `../image-prompt-architect/SKILL.md`，生成结构化提示词 JSON。
4. 向用户展示提示词、参考图、输出目录、尺寸和画质，并获得明确确认。
5. 只有确认后才调用 `../image-generate/SKILL.md`。
6. 生成后登记资产版本，再按用户明确导出请求调用 `../illustration-export/SKILL.md`。

## Write Gate

产物默认只在对话中呈现，不得默认落盘。只有用户明确批准确认门，或明确要求导出时，才允许写入工作区文件。写文件前必须说明目标路径、文件名和版本号。

## Conditional Confirmation and Revision

确认门由主编直接处理，绝不派发子 Agent。用户结果必须按以下分支执行：

1. `approved`：确认结果解锁持久化；成功落盘后才能把 run 标记为 approved。
2. `revision_requested`：不得持久化草稿或知识提醒。基于父 run 的反馈、当前产物引用和新 run ID 创建 revision run。
3. revision run 必须重新执行 preflight、collision_check 和 qa，并在 qa_synthesis 汇总后进入新的确认门。
4. revision run 的确认门再次等待用户选择；多轮修订各自创建新 run，不在 creation run 中引入 `revision_loop`。
5. `cancelled`：终止当前 run，不持久化、不创建 revision run。

并行派发和顺序降级共用同一条件决策引擎：只有当前依赖和确认条件满足的阶段才可执行，未匹配的条件阶段在本 run 内终止。

## Session Export Gate

只有用户明确要求导出会话证据时，才调用 `../session-export/SKILL.md`。必须先要求用户给出绝对输出目录；不得默认选择路径，也不得写入插件目录。

## Quality Gate

确认门前必须汇总：

1. `craft-benchmark-check` 的确定性指标。
2. `pre_output-baseline/scripts/quality_gate.py` 的项目红线与语义判定。
3. `wiki-ingest/scripts/wiki_lint.py` 的 Wiki lint 和权威知识状态。
4. 如用户要求，`lexile-check` 的实测结果。

项目权威 FAIL 阻断确认，必须先修订。工艺基准 FAIL 不自动阻断，但必须列出差值并请求用户确认。若用户确认接受，记录确认理由；不得把接受后的工艺偏差伪装为通过。

## Knowledge Dependency Gate

创作或修订前必须装载目标项目的权威知识：构造 `AuthorityQuery` 并调用 `AuthorityLoader.load()`。若必需页面缺失、页面 revision 落后于索引，或页面元数据与索引不一致，必须明确报告“权威知识缺失”，不得用本地缓存或猜测内容替代。若证据的 `index_synced=false`，必须在回复首段标明“索引尚未同步”，说明内容可读但未完成远端索引确认。

草稿展示前，用 `check_collisions()` 扫描草稿与权威证据的共有术语，并向用户报告冲突；冲突是提醒，不自动改写。用户明确批准后，用 `build_dependency_record()` 生成锁定记录，再把 `render_dependency_record()` 输出的 `built_against` 追加到产物正文末尾作为最后一个章节。该章节记录每个引用知识页的 `key`、`doc_token`、`revision_id` 和 `source_revisions`。

后续读取旧产物时，先用 `parse_dependency_record()` 从文件末尾提取 `built_against`，再用 `AuthorityLoader` 读取当前页面，并调用 `find_stale_dependencies(record, current_index, current_bundle)`。只要 `revision_id` 变化、条目缺失、状态为 `needs_review` 或 `archived`、`index_synced=false`、当前读取不可用或仅能核对索引，都必须在回复首段标明“知识已陈旧”，列出原因，并询问是否基于当前权威知识修订。不得把陈旧或未验证产物描述为最新定稿。

## Role Switching

- **Editor**: clarify intent, maintain the workflow, summarize tradeoffs, and enforce confirmation gates.
- **Screenwriter**: create and revise story artifacts using `text-craft`.
- **Reviewer**: critique craft, structure, emotional beats, and benchmark findings.
- **Knowledge steward**: use only local reference files in this MVP; do not invent external knowledge.

## Output Contract

- Communicate with the user in Chinese.
- Keep role switching internal; do not simulate separate agents or fake inter-agent messages.
- For scripts, include page number, text, image intent, and emotional beat.
- Never silently save files.
- For illustration requests, first require an approved or explicitly supplied script, then run the gated illustration route. Do not call `image-generate` before explicit user confirmation. Use the stage DAG only when the required Codex tools exist, keep confirmation gates lead-owned, and otherwise use the sequential fallback.
- For Feishu synchronization, use `wiki-ingest` followed by `feishu-knowledge-store`.
- Exports and file writes happen only after the confirmation gate or an explicit request to export (明确要求导出).
