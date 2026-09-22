# 条件阶段工作流设计

**日期：** 2026-09-22  
**状态：** 已确认，待编写实施计划  
**范围：** `picturebook-screenwriter` 阶段 DAG、确认门、修订轮次、Codex 多 Agent 派发与顺序降级  
**取代：** `2026-09-21-subagent-stage-dag-design.md` 中关于单 run `revision_loop` 和确认门后并行分支的部分

## 1. 背景与问题

当前阶段 DAG 将条件分支错误地建模为普通依赖：

```text
confirmation_gate
  ├─ revision_loop
  └─ persistence
```

因为 `revision_loop` 与 `persistence` 都只依赖 `confirmation_gate`，调度器在确认门完成后会同时将两者放入 ready 集合。当前测试甚至明确期待二者并行：

- `test_cli_creation_template_parallel_batches`
- `test_confirmation_gate_done_unblocks_persistence_and_revision`

实际业务语义不是并行分支：

- 用户批准时，应进入 `persistence`，不能执行修订。
- 用户要求修改时，应进入修订，不能执行 `persistence`。
- 修订会改变正文，因此旧的 `preflight`、`collision_check` 和 `qa` 结果全部失效。
- 修订完成后必须重新复检，并再次请求用户确认。

当前 `gate` 字段只是非空字符串，不参与调度。`depends_on` 是无条件边，`done` 同时表示“执行成功”和“业务批准”。该模型是带门禁标签的拓扑排序器，不是完整的条件工作流状态机。

## 2. 目标

1. 确认门输出结构化业务结果：`approved`、`revision_requested` 或 `cancelled`。
2. 批准前不得执行 `persistence`。
3. 退回修订时不得执行 `persistence` 或 `knowledge_reminder`。
4. 每一轮修订必须重新执行：
   - `preflight`
   - `collision_check`
   - `qa`
   - `qa_synthesis`
5. 修订完成后必须再次经过 `confirmation_gate`。
6. 单次 run 内部保持无环 DAG；跨 run 的修订轮次由主编控制。
7. 多 Agent 模式与顺序降级模式具有相同业务语义。
8. v1 manifest 必须经过显式迁移才能执行。
9. 保留现有 Task Envelope、Result Envelope、输出路径隔离和并行能力。

## 3. 非目标

1. 不引入通用表达式语言或任意脚本条件。
2. 不把主编替换为外部工作流引擎。
3. 不允许子 Agent 直接修改 run manifest。
4. 不允许子 Agent 直接向用户请求确认。
5. 不在本轮设计中改造插画子图的业务分支。
6. 不让 v1 manifest 通过降级路径绕过确认门。

## 4. 核心业务约束

确认门之后只允许三种结果：

| Outcome | 行为 |
| --- | --- |
| `approved` | 激活 `persistence`，落盘后激活 `knowledge_reminder` |
| `revision_requested` | 当前 run 结束；不落盘；创建下一轮 revision run |
| `cancelled` | 当前 run 取消；不落盘；不创建 revision run |

当 outcome 为 `revision_requested` 时：

- `persistence` 必须为 `skipped`。
- `knowledge_reminder` 必须为 `skipped`。
- 当前 run 的最终状态为：

```json
{
  "status": "completed",
  "outcome": "revision_requested"
}
```

当 outcome 为 `approved` 时，必须先完成 `persistence`，再结束 run：

```json
{
  "status": "completed",
  "outcome": "approved"
}
```

## 5. 总体架构

一次创作或一次修订对应一个 run。Run 内部是无环 DAG，跨 run 的循环由主编管理。

```text
Creation run
  → 完整创作流程
  → confirmation_gate
       ├─ approved
       │    → persistence
       │    → knowledge_reminder
       │    → completed/approved
       │
       ├─ revision_requested
       │    → completed/revision_requested
       │    → 主编创建 Revision run
       │
       └─ cancelled
            → cancelled/cancelled
```

```text
Revision run N
  → revision_init
  → knowledge_load
  → revision_delegate
  → preflight
  → collision_check
  → qa
  → qa_synthesis
  → confirmation_gate
       ├─ approved
       │    → persistence
       │    → knowledge_reminder
       │    → completed/approved
       │
       └─ revision_requested
            → completed/revision_requested
            → Revision run N+1
```

每一轮修订都停在用户确认门，因此不会形成无人值守的无限自动循环。

## 6. Run Manifest v2

### 6.1 Schema

```json
{
  "schema_version": "pb-stage-run-v2",
  "run_id": "20260922-example-0002",
  "root_run_id": "20260922-example-0001",
  "revision_of_run_id": "20260922-example-0001",
  "iteration": 2,
  "intent": "revision",
  "artifact_type": "script",
  "mode": "full",
  "project_root": "/workspace/example",
  "status": "pending",
  "outcome": null,
  "revision_feedback": [
    {
      "page": 5,
      "issue": "页末钩子偏弱",
      "instruction": "加强翻页悬念"
    }
  ],
  "source_artifact_ref": "picturebook/script_v1.md",
  "stages": []
}
```

### 6.2 顶层字段

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| `schema_version` | string | 必须是 `pb-stage-run-v2` |
| `run_id` | string | 单次 run 唯一标识 |
| `root_run_id` | string | 同一作品的所有轮次共享 |
| `revision_of_run_id` | string or null | creation 为 null；revision 必须指向父 run |
| `iteration` | integer | 从 1 开始，revision 必须等于父 iteration + 1 |
| `intent` | string | 至少支持 `creation`、`revision`、`review`、`knowledge`、`illustration` |
| `artifact_type` | string | 沿用现有产物类型枚举 |
| `mode` | string | `full` 或 `light` |
| `project_root` | string | 非空路径 |
| `status` | enum | `pending/running/blocked/completed/failed/cancelled` |
| `outcome` | enum or null | `null/approved/revision_requested/cancelled` |
| `revision_feedback` | array | creation 为空；revision 非空 |
| `source_artifact_ref` | string or null | revision 必须为非空可读引用 |
| `stages` | array | full 模式必须非空；light 模式可为空 |

### 6.3 Run 完成规则

确认门结果决定 run 后续：

| Gate outcome | Run 行为 | 最终状态 |
| --- | --- | --- |
| `approved` | 执行 persistence 和 knowledge_reminder | `completed/approved` |
| `revision_requested` | 跳过 persistence 和 knowledge_reminder | `completed/revision_requested` |
| `cancelled` | 停止剩余阶段 | `cancelled/cancelled` |

## 7. Stage Schema v2

```json
{
  "stage_id": "persistence",
  "assignee": "pb-persistence-agent",
  "depends_on": ["confirmation_gate"],
  "status": "pending",
  "gate": "none",
  "outcome": null,
  "when": {
    "stage_id": "confirmation_gate",
    "outcome": "approved"
  },
  "input_refs": [],
  "output_refs": [],
  "skip_reason": null,
  "blocked_reason": null
}
```

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| `stage_id` | string | run 内唯一 |
| `assignee` | string | 非空 |
| `depends_on` | string[] | 引用已知 stage |
| `status` | enum | `pending/ready/running/blocked/done/skipped/failed/cancelled` |
| `gate` | string | 保留现有门禁标签 |
| `outcome` | string or null | 当前仅确认门要求结构化值 |
| `when` | object or null | 可选条件激活 |
| `input_refs` | string[] | 输入引用 |
| `output_refs` | string[] | 输出引用 |
| `skip_reason` | string or null | skipped 时必须非空 |
| `blocked_reason` | string or null | blocked 时必须非空 |

`skipped` 是新增终态。它表示条件未命中，不表示错误。

## 8. 条件模型

条件使用受限结构，不支持任意表达式：

```json
{
  "stage_id": "<直接依赖 stage>",
  "outcome": "<结构化结果>"
}
```

校验规则：

- `when.stage_id` 必须存在。
- `when.stage_id` 必须属于当前阶段的 `depends_on`。
- `when.outcome` 必须是非空字符串。
- `confirmation_gate` 只允许输出：
  - `approved`
  - `revision_requested`
  - `cancelled`
- 没有 `when` 时使用原有无条件依赖规则。

## 9. 阶段决策

调度决策分为四种：

| Decision | 含义 |
| --- | --- |
| `waiting` | 依赖或结果尚未齐备 |
| `ready` | 可以执行 |
| `skip` | 条件未命中，正常跳过 |
| `block` | 上游失败、阻塞或取消 |

### 9.1 决策顺序

1. 任一依赖为 `failed`、`blocked` 或 `cancelled`，返回 `block`。
2. 任一依赖未进入终态，返回 `waiting`。
3. 有 `when` 时，读取条件 stage 的 outcome。
4. 条件命中返回 `ready`，未命中返回 `skip`。
5. 无 `when` 且全部依赖为 `done`，返回 `ready`。
6. 无 `when` 且存在 `skipped` 依赖，返回 `skip`。
7. 其它情况返回 `waiting`。

### 9.2 状态迁移

新增：

```text
pending → skipped
ready   → skipped
```

`skipped` 为终态：

```text
skipped → 无后续状态
```

确认门迁移：

```text
running → done + approved
running → done + revision_requested
running → done + cancelled
```

禁止：

- `done` 但 outcome 为空。
- 非确认门阶段声明 confirmation outcome。
- `approved` 与 `revision_requested` 同时出现。
- `persistence` 在确认门前进入 ready。

## 10. 调度器接口

```python
validate_manifest(manifest) -> dict
topological_order(manifest) -> list[str]
resolve_stage_decisions(manifest) -> list[StageDecision]
ready_stages(manifest) -> list[str]
next_batches(manifest) -> list[list[str]]
transition_stage(manifest, stage_id, status, **fields) -> dict
finalize_run(manifest, outcome) -> dict
build_revision_manifest(
    parent_run,
    feedback,
    artifact_ref,
    run_id,
) -> dict
```

### 10.1 StageDecision

```python
@dataclass(frozen=True)
class StageDecision:
    stage_id: str
    decision: Literal["waiting", "ready", "skip", "block"]
    reason: str
```

### 10.2 接口职责

| 接口 | 职责 |
| --- | --- |
| `validate_manifest` | schema、依赖、条件与状态字段校验 |
| `topological_order` | 检查无环并生成静态拓扑序 |
| `resolve_stage_decisions` | 以当前状态计算 waiting/ready/skip/block |
| `ready_stages` | 返回当前 ready 阶段 |
| `next_batches` | 返回当前真实可执行批次，不预测未来分支 |
| `transition_stage` | 执行状态迁移并记录原因或 outcome |
| `finalize_run` | 根据确认门结果结束当前 run |
| `build_revision_manifest` | 创建下一轮 revision run |

`parallel_batches()` 不再作为运行时调度入口，因为它无法在 outcome 未知时安全处理条件分支。保留时仅用于兼容的静态拓扑分析，Codex adaptor 与主编使用 `next_batches()`。

## 11. Creation Template v2

```text
session_init
  → brief_gate
  → knowledge_load
  → creation_delegate
  → preflight
  → collision_check
  → qa
  → qa_synthesis
  → confirmation_gate
       ├─ approved
       │    → persistence
       │    → knowledge_reminder
       └─ revision_requested
            → run ends as revision_requested
```

关键条件：

```json
{
  "stage_id": "persistence",
  "assignee": "pb-persistence-agent",
  "depends_on": ["confirmation_gate"],
  "when": {
    "stage_id": "confirmation_gate",
    "outcome": "approved"
  }
}
```

Creation template 不再生成同 run 的 `revision_loop`。

## 12. Revision Template v2

```text
revision_init
  → knowledge_load
  → revision_delegate
  → preflight
  → collision_check
  → qa
  → qa_synthesis
  → confirmation_gate
       ├─ approved
       │    → persistence
       │    → knowledge_reminder
       └─ revision_requested
            → next revision run
```

### 12.1 Revision Init

`revision_init` 必须校验：

- 父 run 状态为 `completed`。
- 父 run outcome 为 `revision_requested`。
- `source_artifact_ref` 存在且可读。
- `revision_feedback` 非空。
- `iteration = parent.iteration + 1`。
- `root_run_id` 与父 run 一致。
- 新 `run_id` 唯一。

任一条件不满足时，revision run 进入 `blocked`。

### 12.2 Revision Delegate

`revision_delegate` 只能处理用户批注，不得：

- 新增未请求的情节或角色。
- 修改用户未指定的产物类型。
- 跳过完整复检。
- 在未批准时写文件。

## 13. Codex Dispatch Plan v2

`stage_dag_codex.py --action plan` 只返回当前可执行内容。

### 13.1 正常可执行

```json
{
  "schema_version": "pb-dispatch-plan-v2",
  "run_id": "20260922-example-0001",
  "iteration": 1,
  "status": "ready",
  "next_batches": [
    {
      "batch_index": 0,
      "stages": ["session_init"]
    }
  ],
  "waiting_for": [],
  "deferred_stages": [
    {
      "stage_id": "persistence",
      "reason": "awaiting confirmation_gate outcome"
    }
  ],
  "decision_required": null,
  "blocked_reasons": []
}
```

### 13.2 等待用户确认

```json
{
  "run_id": "20260922-example-0001",
  "status": "waiting_for_user",
  "next_batches": [],
  "decision_required": {
    "stage_id": "confirmation_gate",
    "owner": "picturebook-screenwriter-team-lead",
    "allowed_outcomes": [
      "approved",
      "revision_requested",
      "cancelled"
    ]
  }
}
```

### 13.3 批准后

```json
{
  "status": "ready",
  "next_batches": [
    {
      "batch_index": 0,
      "stages": ["persistence"]
    }
  ],
  "decision_required": null
}
```

### 13.4 要求修订后

```json
{
  "status": "run_completed",
  "run_outcome": "revision_requested",
  "next_batches": [],
  "follow_up_action": {
    "action": "create_revision_run",
    "parent_run_id": "20260922-example-0001"
  }
}
```

## 14. Sequential Fallback

无多 Agent 工具时：

- 不调用 `spawn_agent`。
- 不提前把 persistence 放入执行列表。
- 主编执行当前 ready stages。
- 到达确认门后停止并等待用户。
- 用户批准后才执行 persistence。
- 用户退回时结束当前 run，并创建 revision run。

顺序 fallback 必须使用与多 Agent 模式相同的条件判断和 run 完成规则。

## 15. 主编与子 Agent 边界

确认门始终由主编所有：

```text
confirmation_gate
asset_confirmation
asset_final_confirmation
```

这些阶段不得派发子 Agent。主编负责：

- 向用户展示确认包。
- 获取 outcome。
- 调用 `transition_stage()` 写入 outcome。
- 调用 `finalize_run()`。
- 必要时调用 `build_revision_manifest()`。

普通子 Agent 保留 `pb-stage-result-v1`，不得直接修改 manifest。

## 16. Persistence 边界

`pb-persistence-agent` 写入前必须验证：

```text
confirmation_gate.status = done
confirmation_gate.outcome = approved
```

否则：

- 不创建或覆盖文件。
- 不更新 `progress.json`。
- 不追加 `built_against`。
- 将阶段标记为 blocked 或 failed。

## 17. 用户可感知行为

批准：

> 已获批准。正在写入 `script_v2.md`，并追加本轮知识依赖记录。

退回：

> 已记录修改意见。当前版本不会落盘。正在创建第 1 轮修订任务，修订后会重新执行红线检查、知识冲突检查和正式质检。

修订复检完成：

> 第 1 轮修订已完成，并重新通过 required checks。当前仍处于待确认状态，尚未保存，请再次确认。

取消：

> 已取消本轮流程。未写入文件，也不会创建修订任务。

## 18. v1 迁移

### 18.1 命令

```bash
python plugins/picturebook-screenwriter/scripts/stage_dag.py \
  --manifest <v1.json> \
  --action migrate
```

默认输出 v2 JSON 到 stdout，不隐式写文件。

### 18.2 迁移规则

| v1 内容 | v2 处理 |
| --- | --- |
| `schema_version=pb-stage-run-v1` | 改为 v2 |
| 缺少 `root_run_id` | 使用原 `run_id` |
| 缺少 `iteration` | 设为 1 |
| 缺少 `status/outcome` | 设为 `pending/null` |
| `revision_loop` | 删除 |
| `persistence` | 增加 approved 条件 |
| `knowledge_reminder` | 保持依赖 persistence |
| 未知 stage | 拒绝迁移 |

无法安全迁移时返回非零并报告结构化错误。

以下 v1 状态视为歧义迁移并必须拒绝：

- `confirmation_gate` 已为 `done`，但没有结构化 outcome。
- `revision_loop` 已开始或完成，但用户最终结果是批准还是继续修订无法证明。
- `persistence` 已开始，但确认门 outcome 无法证明为 approved。
- `revision_loop` 与 `persistence` 同时进入 ready 或完成。

迁移器不得根据阶段名称、正文内容或“另一个分支已经完成”猜测用户决定。

## 19. 错误处理

| 情况 | 处理 |
| --- | --- |
| 非法 confirmation outcome | 拒绝状态迁移 |
| revision 缺少 feedback 或 source | `revision_init` blocked |
| 父 run 非 revision_requested | 拒绝创建 revision run |
| `when` 引用未知 stage | manifest 校验失败 |
| `when` 引用非直接依赖 | manifest 校验失败 |
| 未批准时调度 persistence | 调度器拒绝或阶段 blocked |
| 修订后 preflight/collision/qa 失败 | 不进入确认门，不落盘 |
| 用户取消 | run cancelled，不创建 revision run |
| 再次要求修订 | 创建下一 iteration，不覆盖旧 run |

## 20. 测试要求

### 20.1 Schema 与迁移

- 接受合法 creation v2。
- 接受合法 revision v2。
- 拒绝缺少 revision feedback 的 revision run。
- 拒绝 iteration 未递增。
- 拒绝 root_run_id 不一致。
- 拒绝非法 confirmation outcome。
- 拒绝未知或非直接依赖的 `when`。
- v1 迁移删除 revision_loop 并添加 persistence 条件。
- 拒绝确认门已 done 但 outcome 缺失的歧义历史。
- 拒绝 revision_loop 与 persistence 均已完成的歧义历史。

### 20.2 状态与条件

- confirmation gate 支持三个 outcome。
- confirmation gate 无 outcome 时拒绝 done。
- skipped 只能从 pending/ready 进入。
- skipped 是终态。
- 条件未命中返回 skip。
- 条件命中返回 ready。
- 上游未完成返回 waiting。
- 上游失败返回 block。
- skipped 默认向下游传播。

### 20.3 调度

- approved 后 persistence 进入下一批。
- revision_requested 后 persistence 和 knowledge_reminder 为 skipped。
- persistence 与修订流程永不同批。
- waiting 不生成可执行批次。
- 多 Agent 和顺序 fallback 业务顺序一致。

### 20.4 Revision Run

- 有效父 run 可以创建 revision run。
- 无效父 run 被拒绝。
- 新 run 正确递增 iteration。
- root_run_id 和 revision_of_run_id 正确。
- revision run 重新执行 preflight、collision_check 和 qa。
- 修订完成后再次停在 confirmation_gate。
- 连续两轮修订保留独立审计记录。

### 20.5 Codex Adapter

- 正常阶段返回 next_batches。
- 确认门返回 waiting_for_user。
- approved 后返回 persistence 批次。
- revision_requested 后返回 create_revision_run。
- confirmation gate 不派发子 Agent。
- revision task envelope 注入 iteration、feedback 和 source artifact。

### 20.6 端到端

1. creation → checks → qa → gate/approved → persistence。
2. creation → checks → qa → gate/revision_requested → revision run。
3. revision run → checks → qa → gate/approved → persistence。
4. revision run 1 → revision_requested → revision run 2 → approved。
5. gate/cancelled → no persistence → no revision run。

### 20.7 回归

- preflight 与 collision_check 并行能力不变。
- Task Envelope 与 Result Envelope 兼容。
- output path 越界检查保持失败关闭。
- light 模式不创建 stages。
- illustration 模板不受 creation/revision 改造影响。
- 现有插件测试全部通过。

## 21. 实施顺序

| 阶段 | 交付 |
| --- | --- |
| 1 | Run/Stage v2、状态与 outcome、迁移器、schema 测试 |
| 2 | 条件求值、skipped、next_batches、状态迁移测试 |
| 3 | Creation/Revision templates、finalize_run、build_revision_manifest |
| 4 | Codex adapter、sequential fallback、用户确认交互 |
| 5 | stage-orchestration Skill、入口 Skill、plugin contract |
| 6 | 端到端测试、全量回归、v1 迁移演练 |

每个阶段独立通过测试并独立提交。

## 22. 验收标准

1. confirmation gate 只允许 approved、revision_requested、cancelled。
2. approved 前 persistence 永远不能进入 ready。
3. revision_requested 后 persistence 和 knowledge_reminder 永久 skipped。
4. revision run 必须重新执行 preflight、collision_check 和 qa。
5. 修订轮次拥有独立 run_id、iteration 和父 run 关系。
6. 每轮修订都停在新的确认门。
7. 用户可明确看到“尚未落盘，等待确认”。
8. v1 manifest 不能绕过迁移执行。
9. 多 Agent 与顺序 fallback 行为一致。
10. 现有非修订 DAG 行为保持兼容。
