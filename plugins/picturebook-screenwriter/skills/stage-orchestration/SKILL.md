---
name: stage-orchestration
description: "Codex-native stage DAG orchestration. Validates stage manifests, plans parallel dispatch batches via spawn_agent/wait_agent, and provides sequential fallback when multi-agent tools are unavailable."
---

# 阶段 DAG 编排

本技能把 Codex 会话的任务编排升级为显式 DAG + 多 Agent 并行派发。
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
python plugins/picturebook-screenwriter/scripts/stage_dag.py --action illustration-template
python plugins/picturebook-screenwriter/scripts/stage_dag.py --manifest <path> --action validate
python plugins/picturebook-screenwriter/scripts/stage_dag.py --manifest <path> --action batches
python plugins/picturebook-screenwriter/scripts/stage_dag_codex.py --manifest <path> --action plan
python plugins/picturebook-screenwriter/scripts/stage_dag_codex.py --manifest <path> --action fallback
```

## Codex 派发流程

1. 用 `creation-template` 或 `illustration-template` 生成 manifest JSON。
2. 用 `stage_dag_codex.py --action plan` 生成派发计划。
3. 按计划中每个批次的 stage 逐个调用 `spawn_agent`（同一批次可并行）。
   - `fork_context: false` 给子 Agent 干净上下文。
   - 把 task envelope 中的 stage 信息 + 对应技能（如 `text-craft`、`craft-benchmark-check`）注入 message。
4. 用 `wait_agent` 等待当前批次完成。
5. 检查每个子 Agent 回传的结果是否符合 `pb-stage-result-v1` 结构。
6. 用 `stage_dag.transition_stage` 更新 manifest 状态。
7. 重复直到所有阶段 `done` 或进入 `blocked`（等待用户确认）。

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
这些阶段不派发子 Agent，由主编直接向用户呈现并等待回复。

## 顺序降级

当 `spawn_agent` 不可用时，用 `stage_dag_codex.py --action fallback` 输出顺序
执行计划，然后主编按批次顺序逐个执行每个阶段的工作，不派发子 Agent。
