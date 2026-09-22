---
name: stage-orchestration
description: "Codex-native conditional stage DAG orchestration. Validates v2 stage manifests, plans current dispatch batches via spawn_agent/wait_agent, and provides sequential fallback using the same decision engine."
---

# 阶段 DAG 编排

本技能把 Codex 会话的任务编排升级为显式条件 DAG + 可选多 Agent 并行派发。
当前会话仍是唯一用户入口；子 Agent 通过 `spawn_agent` 派发，结果通过
`wait_agent` 等待，续跑通过 `send_input`。

## 前置条件

- Codex 配置 `config.toml` 中 `[features] multi_agent = true`。
- 当前会话的工具列表中有 `spawn_agent`、`wait_agent`、`send_input`。
- 如果没有这些工具，主编必须先向用户声明「顺序降级模式」。

## 命令

命令以插件 `scripts/` 目录为锚：

```bash
python plugins/picturebook-screenwriter/scripts/stage_dag.py --action creation-template
# revision-template 只用于查看结构；实际 revision run 必须调用 build_revision_manifest()
python plugins/picturebook-screenwriter/scripts/stage_dag.py --action revision-template
python plugins/picturebook-screenwriter/scripts/stage_dag.py --action illustration-template
python plugins/picturebook-screenwriter/scripts/stage_dag.py --manifest <path> --action validate
python plugins/picturebook-screenwriter/scripts/stage_dag.py --manifest <path> --action batches
python plugins/picturebook-screenwriter/scripts/stage_dag_codex.py --manifest <path> --action plan
python plugins/picturebook-screenwriter/scripts/stage_dag_codex.py --manifest <path> --action fallback
```

## Codex 派发流程

creation、revision 和 illustration 模板均使用 `pb-stage-run-v2`。creation 模板
不得包含 `revision_loop`；持久化阶段以 `confirmation_gate` 的 `approved`
结果为条件。revision run 由 `build_revision_manifest()` 从已请求修订的父 run
创建，并重新执行 preflight、collision_check、qa 和 qa_synthesis。

1. 用 `creation-template` 或 `illustration-template` 生成初始 manifest JSON；
   修订时用 `build_revision_manifest()` 创建 revision run。
2. 用 `stage_dag_codex.py --action plan` 生成当前批次的派发计划。
   计划基于条件决策引擎，只返回当前可执行的 `lead_actions` 和
   `next_batches`；只有带 `when` 条件且尚未满足的后续阶段会进入
   `deferred_stages`，仅因依赖尚未完成而等待的阶段不会列出。不得把计划
   解释为未来无条件批次。
3. 若计划状态为 `waiting_for_user`，说明当前待执行的确认门必须由主编
   直接向用户呈现，不能派发子 Agent，也不能自动选择结果。等待用户返回
   `approved`、`revision_requested` 或 `cancelled` 后，再用
   `stage_dag.transition_stage` 写入确认结果并重新生成计划。
4. 若计划状态为 `ready`，按 `next_batches` 中每个批次的 stage 逐个调用
   `spawn_agent`（同一批次可并行）。
   - `fork_context: false` 给子 Agent 干净上下文。
   - 把 task envelope 中的 stage 信息 + 对应技能（如 `text-craft`、`craft-benchmark-check`）注入 message。
5. 用 `wait_agent` 等待当前批次完成。
6. 检查每个子 Agent 回传的结果是否符合 `pb-stage-result-v1` 结构。
7. 用 `stage_dag.transition_stage` 更新 manifest 状态。
8. 每次状态变化后重新生成派发计划，直到 run 完成、取消或再次进入
   `waiting_for_user`。

## 阶段 → 技能映射

Codex 没有 WorkBuddy 式的预定义角色文件。`assignee` 字段表示的是该阶段需要注入哪些技能：

| assignee | 注入技能 |
|---|---|
| `pb-intake-agent` | 无（纯结构化准备） |
| `pb-knowledge-steward` | `knowledge-loader`、`feishu-knowledge-store` |
| `pb-screenwriter` | `text-craft`、对应产物类型技能 |
| `pb-preflight-agent` | `craft-benchmark-check`、`pre_output-baseline` |
| `pb-quality-reviewer` | `quality-baseline` |
| `pb-persistence-agent` | 无（纯文件写入） |
| `picturebook-art-agent` | `image-prompt-architect`、`image-generate`、`illustration-export` |
| `picturebook-screenwriter-team-lead` | 无（主编自己执行） |

子 Agent 的 `message` 应包含：

1. Task envelope JSON。
2. 上述映射表中列出的技能的完整内容（读取 SKILL.md 后注入）。
3. 一句话说明"你不需要直接与用户交互，结果以 JSON 形式返回给主编"。

## 确认门

`confirmation_gate`、`asset_confirmation`、`asset_final_confirmation` 阶段的
`assignee` 是 `picturebook-screenwriter-team-lead`（即当前 Codex 会话自己）。
这些 lead-owned gates 不得派发子 Agent，由主编直接向用户呈现并等待回复。
即使 manifest 中的 assignee 被错误配置为非主编，适配层也必须返回
`waiting_for_user`，不会把确认门放入派发批次。

确认结果只能是 `approved`、`revision_requested` 或 `cancelled`：

- `approved`：确认阶段以 `approved` 完成后，计划才会解锁条件阶段
  `persistence`；只有该阶段成功完成，run 才能持久化为 approved。
- `revision_requested`：不得执行 `persistence` 或 `knowledge_reminder`；
  `finalize_run()` 会根据该结果把这些阶段标记为终态 `skipped`。派发计划返回
  `follow_up_action.create_revision_run`，主编再用
  `build_revision_manifest(parent_run, feedback, artifact_ref, run_id)` 创建
  新的 revision run。revision run 必须完整重跑 preflight、collision_check、
  qa 和 qa_synthesis，再进入自己的确认门。
- `cancelled`：run 终止，不创建 revision run，也不落盘未批准产物。

条件不匹配而 `skip` 的阶段在本 run 内是终态；不得跳过确认门直接执行下游，
也不得在 creation manifest 中补回 `revision_loop`。

## 顺序降级

当 `spawn_agent` 不可用时，用 `stage_dag_codex.py --action fallback` 输出顺序
执行计划，然后主编按批次顺序逐个执行每个阶段的工作，不派发子 Agent。
fallback 与并行模式使用同一个条件决策引擎，因此同样返回当前批次、
`waiting_for_user` 确认门和 `revision_requested` 后续动作；顺序执行不得绕过
任何确认结果或重新引入无条件 `revision_loop`。
