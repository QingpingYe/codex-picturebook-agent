# Jev 辅助决策层设计

**日期：** 2026-09-23

**状态：** 已完成设计，待用户审阅

**范围：** `picturebook-screenwriter` 的首次执行选择、共享 Jev 运行器、执行观测、权威知识分块相关性筛选、红线与逐页文本质量预筛

**不取代：** 当前普通 LLM 创作、解释性终审、确认门、确定型脚本和权威知识校验

## 1. 背景与结论

当前插件将三类工作混在同一条模型工作流里：

1. 代码能够精确完成的工作，例如页数、字符量、版本向量、DAG 状态和几何投影。
2. 答案空间明确、但需要自然语言常识的小判断，例如一个知识片段是否相关、一页文字是否有直接说教倾向。
3. 需要生成、长程推理、整体审美或可解释结论的工作，例如写故事、修改情感弧线、解释为什么一条红线构成触犯。

Jev 只适合第 2 类。它返回 `Noul`、`Choice` 或 `Score` 类型化概率，不生成解释性文字；类型安全只保证输出契约，不保证判断必然正确。第 1 类继续归确定型脚本，第 3 类继续归普通 LLM。

本设计不把 Jev 建模为普通 LLM 的替代供应商，而是新增一条用户显式选择的 **Jev 辅助路径**：

```text
用户选择普通 LLM
  → 保持现有流程

用户选择 Jev 辅助
  → 确定型候选生成
  → Jev 做原子判断与概率路由
  → experimental operation：保留普通 LLM 全量复核，用于校准
  → calibrated operation：仅把高风险、灰区和失败项交给普通 LLM
  → 普通 LLM 仍负责解释、修改建议和最终创作判断
```

首轮只落地两个高复用操作：

- `knowledge_relevance`：权威知识分块相关性筛选。
- `text_quality_prefilter`：红线及逐页文本质量预筛。

## 2. 已核实的能力边界

截至 2026-09-23，TypeSafe 官方资料确认：

- `jev-1.13.0` 使用 `POST /v1/systemone`，输入价格为每百万 token 0.042 美元，输出 token 免费。
- 单请求总上下文为 64k token，`state + 最长单题` 上限为 32k token。
- 输入仅支持文本或文本化 JSON，不支持图片、音频或视频。
- 英语是主要训练语言；中文和其他 CJK 内容可以处理，但准确率不如英语，必须用自己的数据验证。
- Jev 不可靠地执行计数、算术、日期比较和多层间接推理；这些工作应留在代码中。
- Jev 不用于文本生成；需要生成文字时继续使用生成模型。
- 大量无关 state 会降低准确率，应先由代码召回和切分，再把窄上下文交给 Jev。
- `Choice`/`Score` 的 `confidence` 是分布集中度，不是正确率；`Noul` 没有独立 `confidence`。所有阈值必须按本地样本校准。

依据：

- https://docs.typesafe.ai/models
- https://docs.typesafe.ai/model-jaggedness/jev-1.13
- https://docs.typesafe.ai/primitives
- https://docs.typesafe.ai/confidence
- https://github.com/typesafe-ai/skills/blob/main/skills/typesafe-ai/SKILL.md

文档《TypeSafe调研结论与绘本编剧工坊质检启发.md》关于“不能替代可解释终审”“计数留在代码”“原子问题由代码组合”的方向成立。文档中的第三方精确性能数字未提供全部原始复现实验链接，因此不作为本设计的验收基线；本插件必须自行测量中文绘本样本。

## 3. 目标

1. 用户每次启动 `Picture Book Screenwriter` 顶层工作流时，第一件事就是明确选择是否使用 Jev。
2. 普通 LLM 路径保持现有行为，不因新增 Jev 代码而改变结果契约。
3. Jev 路径必须清楚说明：相关故事文本和知识片段会发送给 TypeSafe API。
4. 建立共享 Jev 运行器，统一处理请求契约、凭证、超时、重试、版本、阈值、成本和审计信息。
5. 用确定型切分加 Jev 概率判断缩减送入普通 LLM 的知识上下文。
6. 用 Jev 对全部活动红线和逐页质量维度做原子预筛，只把高风险、灰区和异常项升级给普通 LLM。
7. 为普通 LLM 与 Jev 辅助两种路径生成同口径运行记录，便于结合 CC Switch 比较耗时、token 和成本。
8. Jev 缺 key、超时、返回不完整或调用失败时，不得把任何项目判为通过，也不得静默切换执行路径。
9. 保持项目权威、用户确认、写入许可和外部副作用仍由现有流程控制。

## 4. 非目标

1. 不让 Jev 生成故事、润色正文、撰写修改建议或生成插画提示词。
2. 不让 Jev 读取或生成图片。
3. 不用 Jev 计算页数、词量、重复次数、句长、蓝思值、几何坐标或费用。
4. 不替换 `craft-benchmark-check`、`project_scene_map.js`、DAG 状态机、知识 revision 校验或提示词 schema 校验。
5. 不让 Jev 自动批准用户确认门、自动落盘、自动同步飞书或执行其他有副作用操作。
6. 不在首轮接入插画风险目录、站位依据判断、修改反馈路由或 Wiki 候选归纳；这些作为后续扩展点。
7. 不直接读取或依赖 CC Switch 的本地数据库。CC Switch 版本和存储结构不是本插件的稳定接口。
8. 不把厂商演示阈值、灰区或性能数字直接当作本项目阈值。

## 5. 用户入口与选择门

### 5.1 第一响应规则

入口技能一旦匹配用户请求，在执行以下任何动作前，必须先建立执行选择：

- 意图分类；
- 简报追问；
- 飞书或本地知识读取；
- 子 Agent 派发；
- 创作、修改或质检；
- 外部 API 调用；
- 文件写入。

第一响应只询问一次：

```text
本次是否启用 Jev 辅助？

- 不启用：沿用普通 LLM 完整流程。
- 启用：将相关故事文本和知识片段发送给 TypeSafe Jev，
  用于知识筛选及文本质检预筛；创作、解释性终审和修改建议仍由普通 LLM 完成。
  尚未校准的检查会保留普通 LLM 全量复核，用于比较和校准。
```

该问题不得与简报问题合并。用户未回答时，工作流保持 `waiting_for_execution_choice`，不继续执行。

### 5.2 选择生命周期

- 选择作用于一个顶层 root run 及其 revision runs。
- 同一 root run 内不重复询问，除非用户明确要求切换。
- 新的顶层创作、审稿、知识或插画请求必须重新询问。
- 仅支持两个正式值：`llm` 与 `jev_assisted`。
- 若用户选择 Jev，但当前意图没有首轮支持的 Jev 操作，仍记录选择，并明确说明该阶段暂时沿用普通 LLM 或确定型脚本。
- 用户可在任何未产生外部副作用的阶段请求切换。切换后新操作使用新路径，既有结果保留原执行来源。

### 5.3 Jev 不可用时

用户选择 `jev_assisted` 后才检查 `TYPESAFE_API_KEY`。以下情况必须暂停并让用户选择“改用普通 LLM”或“停止本次工作流”：

- 缺少 key；
- 401/403；
- 422 请求契约错误；
- 限次重试后仍为 429/529；
- 网络超时或响应 schema 不完整；
- 返回模型版本不符合已校准策略。

不得静默回退普通 LLM，因为这会破坏用户对速度和成本路径的选择，也会使对比数据失真。

## 6. 总体架构

### 6.1 组件

```text
picturebook-screenwriter 入口技能
  └─ Execution Choice Gate
       ├─ llm ───────────────→ 现有工作流
       └─ jev_assisted
            └─ Jev Decision Runtime
                 ├─ knowledge_relevance
                 ├─ text_quality_prefilter
                 ├─ HTTP client / retry / schema validation
                 ├─ policy + thresholds
                 └─ execution trace / cost calculation
```

共享运行器作为插件内部 utility skill 提供，建议目录：

```text
plugins/picturebook-screenwriter/skills/jev-decision-runtime/
  SKILL.md
  references/
    decision-policies.json
  scripts/
    decision_contract.py
    jev_client.py
    jev_runner.py
    telemetry.py
    test_decision_contract.py
    test_jev_client.py
    test_jev_runner.py
    test_telemetry.py
```

入口技能、`knowledge-loader`、`pre_output-baseline` 与 `quality-baseline` 只依赖共享运行器，不各自实现 HTTP、重试或计费逻辑。

### 6.2 与 Stage DAG 的关系

Stage DAG 继续只负责状态、依赖和确认条件，不引入 `jev` assignee，也不新增 Jev stage。

选择以向后兼容的 `decision_context` 附加到 run manifest 和 task envelope：

```json
{
  "schema_version": "pb-decision-context-v1",
  "mode": "jev_assisted",
  "selection_status": "confirmed",
  "selected_at": "2026-09-23T10:30:00+08:00",
  "external_text_processing_acknowledged": true
}
```

规则：

- 不存储 API key。
- v2 历史 manifest 缺少 `decision_context` 时仍可读取；恢复执行前必须先进入选择门。
- 新建 run 必须包含已确认的 `decision_context`。
- revision run 继承 root run 的选择，用户显式切换时记录新的 `selected_at`。
- `stage_dag` 的条件决策不读取 `mode`，避免模型供应商语义进入通用调度器。

## 7. 共享 Jev 运行器

### 7.1 请求契约

运行器接受 `pb-jev-request-v1`：

```json
{
  "schema_version": "pb-jev-request-v1",
  "run_id": "20260923-example-0001",
  "operation": "knowledge_relevance",
  "model": "jev-1.13.0",
  "policy_version": "knowledge-relevance-v1",
  "state": {},
  "questions": {},
  "context_refs": [],
  "benchmark_case_id": "sha256:..."
}
```

约束：

- `operation` 首轮只允许 `knowledge_relevance` 或 `text_quality_prefilter`。
- `model` 固定为当前策略所针对的版本 ID，不默认使用会漂移的 `jev-latest`；策略是否已校准由独立字段表达。
- `state` 只包含当前判断所需的最小文本或结构化字段。
- `questions` 的 ID 只用于代码映射；每条指令必须自包含，不能依赖其他题的答案。
- `context_refs` 记录知识 key、revision、页码或规则 ID，不记录凭证。
- `benchmark_case_id` 由规范化输入、策略版本和规则版本计算，使两种路径可确认使用同一测试样本。

### 7.2 响应契约

运行器输出 `pb-jev-result-v1`：

```json
{
  "schema_version": "pb-jev-result-v1",
  "run_id": "20260923-example-0001",
  "operation": "knowledge_relevance",
  "provider": "typesafe",
  "requested_model": "jev-1.13.0",
  "resolved_model": "jev-1.13.0",
  "policy_version": "knowledge-relevance-v1",
  "answers": {},
  "routes": [],
  "usage": {
    "input_tokens": 0,
    "output_tokens": 0
  },
  "trace": {},
  "status": "succeeded"
}
```

`answers` 保留原始概率，不把单个概率重写为“模型正确率”。`routes` 是代码根据当前 policy 计算出的 `include`、`exclude_soft`、`escalate_llm` 或 `needs_user_choice`。

### 7.3 HTTP 与重试

- Endpoint 默认 `https://api.typesafe.ai/v1/systemone`。
- 凭证只从 `TYPESAFE_API_KEY` 读取。
- 可通过显式配置覆盖 base URL，主要用于测试；不得把 key 写进配置、日志或命令行示例。
- 429/529 按 `retry-after` 或有上限的指数退避重试。
- 401/403/422 不做盲重试。
- 每次请求设置连接和总超时；达到上限后返回结构化失败。
- 部分答案、未知题 ID、错误原语类型或非有限概率均视为响应不完整。

### 7.4 策略与阈值

`decision-policies.json` 保存：

- 固定模型版本；
- operation 的问题模板和原语类型；
- 灰区与升级规则；
- 策略版本；
- 校准状态：`experimental` 或 `calibrated`；
- 价格快照及日期。

首版策略状态为 `experimental`。由于中文/CJK 准确率尚未在本项目验证：

- 项目硬约束绝不因低概率而删除。
- 不完整或不确定结果一律保留上下文或升级普通 LLM。
- 只有软知识片段可被 `exclude_soft`。
- 只有原子预筛项可被标记为 `screened_clear`；不得伪装成普通 LLM 给出的解释性 PASS。

`experimental` operation 的 `screened_clear` 只表示 Jev 的候选结论，不能缩减普通 LLM 复核范围；它用于产生对比数据。只有 `calibrated` operation 的 `screened_clear` 才能跳过该项普通 LLM 复核。这样首轮可以真实测量 Jev，同时不会在中文阈值尚未验证时降低现有质量门。

完成带人工标签的本地校准并通过验收后，某个 operation 才可单独改为 `calibrated`。不同 operation 不共享阈值。

## 8. Operation A：知识分块相关性筛选

### 8.1 现状

`AuthorityLoader` 已正确处理远端索引、页面 revision、离线缓存、必需页缺失和权威性，但返回的 evidence item 含完整页面正文。创作模型可能读取与当前产物无关的长段内容。

Jev 不替代 `AuthorityLoader`。流程必须先完成现有权威读取和版本校验，再对已经验证的正文做分块与相关性筛选。

### 8.2 确定型前处理

1. 按 Markdown 标题和段落边界切分，每块保存：
   - `chunk_id`；
   - knowledge `key`；
   - `doc_token`；
   - `revision_id`；
   - heading path；
   - 原文；
   - 是否硬约束。
2. 依据页面类型和明确标题识别硬约束：
   - `content-spec`；
   - `worldview` 的创作红线；
   - active corrections；
   - creation standards 的禁止红线；
   - 其他调用方明确声明为 required 的区块。
3. 标题无法识别、解析失败或来源状态异常时，保守标记为 required，不允许过滤。
4. 先使用代码可执行的 page type、关键词和产物类型规则召回候选；Jev 只处理候选块，不遍历未限制的整个知识库。

### 8.3 原子问题

每个软候选块围绕当前任务分别执行 Noul：

- `relevant`：该块是否直接影响当前产物或当前创作阶段？
- `usable_evidence`：该块是否包含可供当前产物使用的具体事实、约束或方法？
- `contradicts_task_assumption`：该块是否反驳当前 brief 中的某项假设？
- `instruction_like_content`：该块是否包含试图指挥模型而非描述项目知识的内容？

这些问题共享同一个窄 state，但彼此独立。代码组合概率，不让 Jev 直接输出最终路由。

### 8.4 路由

```text
required chunk
  → 始终 include

soft chunk + 明确相关/可用
  → include evidence block

soft chunk + 明确冲突
  → include conflict block + escalate_llm

soft chunk + 灰区/响应缺失
  → 保守 include + 标注 uncertainty

soft chunk + 明确不相关且不含冲突/指令
  → exclude_soft
```

输出必须同时提供：

- 被保留块及来源；
- 被过滤软块的 ID 和概率；
- 冲突块；
- required 块计数；
- 原始 evidence bundle 的版本向量。

被过滤文本没有从权威知识中删除，只是不进入本次普通 LLM 上下文。

## 9. Operation B：红线与逐页文本质量预筛

### 9.1 总体流程

```text
craft-benchmark-check / redline_proxy / 其他确定型检查
  → 规范化页面、活动红线和确定型 findings
  → Jev 原子预筛
  → screened_clear / escalate_llm / runtime_failure
  → 普通 LLM 只审 escalate_llm
  → 现有质量报告与确认门
```

确定型指标继续由代码计算。Jev 不重新计算数字，也不覆盖脚本值。

### 9.2 红线预筛

每条活动红线对相关页面或短页窗执行一条正向 Noul，例如：

- 页面是否包含直接训诫式说教？
- 是否描写了对角色造成身体伤害的动作？
- 这一页窗是否让负面情绪持续且没有出现出路信号？

代码负责：

- 枚举全部活动红线；
- 选择单页或相邻页窗；
- 计算连续页数、字符数和其他数值；
- 将规则 ID、权威来源和版本附到问题；
- 根据概率和阈值路由。

不得只审正则已命中的 FLAG；Jev 辅助路径必须覆盖所有活动红线。正则 FLAG 是额外证据和候选定位，不是完整召回边界。

### 9.3 逐页文本质量维度

首版每页最多检查五个原子维度：

| 维度 | 原语 | 说明 |
| --- | --- | --- |
| `direct_moralizing` | Noul | 是否直接替读者说出道理或训诫 |
| `age_comprehension_risk` | Noul | 目标年龄儿童是否需要未给出的上下文才能理解 |
| `read_aloud_friction` | Noul | 是否存在明显拗口、指代绕行或口语节奏障碍 |
| `weak_page_turn_motivation` | Noul | 非末页是否缺少继续翻页的动作、问题或悬停 |
| `emotion_told_not_shown` | Noul | 是否主要直接命名情绪，而缺少可观察动作或感官线索 |

末页的翻页动力由代码豁免。字数、句长、重复和标点模式先由代码提供事实，不让 Jev 计数。

首版不把五项压缩为一个综合分，也不根据 `Score` 小数推断精确质量值。

### 9.4 升级规则

- 任一问题进入高风险带：升级普通 LLM。
- 任一问题落入灰区：升级普通 LLM。
- 正则 FLAG 与 Jev 结论冲突：升级普通 LLM。
- 项目权威来源缺失、陈旧或未同步：沿用现有 fail-closed 行为，不由 Jev裁决。
- `calibrated` operation 的所有问题均处于清晰低风险带：标记 `screened_clear`，不调用该项普通 LLM 复核。
- `experimental` operation 即使全部处于低风险带，也保留普通 LLM 全量复核，并把分歧写入校准记录。

普通 LLM 对升级项必须继续输出：

- PASS/WARN/FAIL；
- 引用草稿原文；
- 分析；
- FAIL 的修复建议。

### 9.5 修正现有 finding 语义

当前 `redline_proxy.py` 产生的字面命中使用 `source="project"`、`severity="FAIL"`，而 `apply_judgments()` 又允许语义判断将其降为 PASS。这混淆了“代理命中”与“权威确认”。实现前必须拆分：

- `project_proxy`：廉价扫描产生的候选，可由后续语义判断消歧。
- `project_authority`：经过解释性终审确认的项目红线触犯，必须阻断确认门。

Jev 结果使用独立的 `ScreeningDecision`，不得伪装为 `SemanticJudgment`。只有普通 LLM 或现有明确授权的终审过程能产生最终 `SemanticJudgment`。

## 10. 测速、token 与成本记录

### 10.1 观测契约

每次 operation 生成 `pb-decision-trace-v1`：

```json
{
  "schema_version": "pb-decision-trace-v1",
  "run_id": "20260923-example-0001",
  "benchmark_case_id": "sha256:...",
  "execution_mode": "jev_assisted",
  "operation": "text_quality_prefilter",
  "started_at": "2026-09-23T10:30:00.000+08:00",
  "finished_at": "2026-09-23T10:30:00.412+08:00",
  "elapsed_ms": 412,
  "input_sha256": "...",
  "item_count": 32,
  "question_count": 160,
  "screened_clear_count": 21,
  "escalated_count": 11,
  "request_count": 4,
  "input_tokens": 8200,
  "output_tokens": 640,
  "estimated_cost_usd": "0.000344400000",
  "price_usd_per_million_input_tokens": "0.042",
  "price_snapshot_date": "2026-09-23",
  "resolved_model": "jev-1.13.0",
  "status": "succeeded",
  "fallback_used": false,
  "error_class": null
}
```

规则：

- 成本用十进制定点计算，不用二进制浮点拼账单。
- 原始 token 用量始终保留，费用只标为 estimate。
- 价格可由显式配置覆盖；报告同时保存单价和快照日期。
- 缺失 usage 时，token 和成本为 `null`，不得用字符数伪造精确 token。
- trace 不存原始知识正文、草稿、API key、Authorization header 或完整响应。
- `fallback_used` 只有用户明确同意切换路径后才能为 true。

### 10.2 普通 LLM 与 CC Switch

插件无法从当前 Codex 会话稳定获得逐请求 token 和实际账单。普通 LLM 路径记录：

- 同一 `benchmark_case_id`；
- operation 开始/结束时间窗；
- 输入 item 数和候选数；
- 当前执行路径；
- 可获得时的模型标签。

CC Switch 负责提供 Codex 请求的 model、input/output/cache tokens、请求耗时和估算成本。用户按 trace 时间窗、Codex app 和模型过滤请求，把汇总值填入比较报告。

本插件不直接访问 CC Switch 数据库。原因是其数据库 schema 不是稳定公共契约，且会引入个人环境路径。后续如 CC Switch 提供稳定导出 API，可再增加可选导入器。

### 10.3 对比报告

两次运行只有在以下字段一致时才并排比较：

- `benchmark_case_id`；
- 输入知识 revision vector；
- 草稿 hash；
- policy/rule version；
- 目标年龄、体裁和页数参数。

报告至少包含：

| 指标 | 普通 LLM | Jev 辅助 |
| --- | --- | --- |
| 端到端耗时 | CC Switch / 时间窗 | Jev + 升级 LLM 时间窗 |
| LLM 请求数 | CC Switch | CC Switch |
| LLM input/output/cache token | CC Switch | CC Switch |
| Jev 请求数与 token | 0 | trace |
| 估算总成本 | CC Switch | CC Switch + Jev estimate |
| 进入 LLM 的知识块数 | 全量 | 筛选后 |
| 进入 LLM 的质量项数 | 全量 | 升级项 |
| 发现问题数 | 结果记录 | 结果记录 |
| 漏检/分歧 | 人工标注 | 人工标注 |

默认只在对话中展示运行摘要。只有用户明确要求导出时，才通过现有 `session-export` 能力写出 JSON/Markdown 比较报告，保持当前写入门约束。

## 11. 中文校准与上线纪律

### 11.1 校准样本

每个 operation 使用独立样本集：

- `knowledge_relevance`：当前任务与知识块对，人工标签至少包括 required、relevant、irrelevant、conflict、uncertain。
- `text_quality_prefilter`：按页和红线拆分的判断项，人工标签至少包括 clear、issue、borderline，并保存引用证据。

样本应覆盖：

- 中文儿童口语；
- 重复句、副歌、拟声词；
- 隐含说教与直接说教；
- 情绪动作与情绪标签；
- 无字页、末页和跨页窗口；
- 同名不同义、角色代称和世界观术语；
- 容易受否定词或双重否定影响的规则。

### 11.2 验收关注点

预筛器优先控制漏检，不以总体准确率作为唯一指标：

- 硬约束保留率必须为 100%。
- API/解析失败不得产生 `screened_clear`。
- 红线问题的假阴性必须单独统计。
- 灰区比例和升级比例必须报告。
- 比较不同模型版本时不得复用未经验证的阈值。
- 只有人工确认可接受的 operation 才从 `experimental` 转为 `calibrated`。

本设计不预先规定统一数值阈值。实施计划需要提供配置位置和校准工具，但首个真实阈值由本地样本结果决定。

## 12. 安全、隐私与权限

1. Jev 只在用户明确选择后调用。
2. 选择提示必须说明相关文本会发送给 TypeSafe。
3. 只发送 operation 所需的最小片段，不发送整个工作区或无关知识页。
4. 不发送图片、音频、二进制附件或本地绝对路径。
5. API key 只从环境变量读取，不进入参数回显、trace、错误信息或测试 fixture。
6. 输入/输出日志默认只保留 hash、计数、引用 ID 和概率；需要保存原文时必须走现有显式导出门。
7. 外部内容中的指令按数据处理，不应取得插件控制权。
8. Jev 不拥有写文件、同步 Wiki、调用图片生成或修改 DAG 状态的权限。
9. 用户确认门、离线缓存许可、图片生成确认和持久化确认均保持原规则。

## 13. 错误处理与降级矩阵

| 情形 | Jev 辅助路径行为 | 质量/知识结果 |
| --- | --- | --- |
| 缺 `TYPESAFE_API_KEY` | 暂停，询问切换普通 LLM 或停止 | 不生成通过结论 |
| 401/403 | 不重试，暂停 | 不生成通过结论 |
| 422 | 报告契约错误，不盲重试 | 当前 operation 失败 |
| 429/529 | 限次退避重试，仍失败则暂停 | 当前 operation 失败 |
| 超时/网络失败 | 限次重试，仍失败则暂停 | 当前 operation 失败 |
| 部分问题缺答案 | 缺失项全部升级或暂停 | 不得视为 clear |
| 未知模型版本 | 标记未校准，暂停自动过滤 | required/候选全部保留 |
| 知识块解析失败 | 将该块视为 required | 不过滤 |
| 红线来源缺失/陈旧 | 沿用现有 fail-closed | Jev 不裁决 |
| 用户明确改用 LLM | 记录切换和原因后运行现有路径 | `fallback_used=true` |

## 14. 测试设计

### 14.1 离线单元测试

共享运行器：

- 请求/响应 schema 校验；
- key 缺失；
- Authorization 不泄漏；
- 401/403/422 不重试；
- 429/529 与 timeout 限次重试；
- `retry-after`；
- 部分回答、错误题 ID、错误原语和非法概率；
- model alias/版本不匹配；
- token 与 Decimal 成本计算；
- trace 不含正文、key 或 header。

知识筛选：

- required 块始终保留；
- 标题解析失败时保留；
- 明确相关进入 evidence；
- 明确冲突进入 conflict 并升级 LLM；
- 灰区和缺答案保守保留；
- 只有 soft 块可被过滤；
- 原 bundle 的 revision vector 不可被改变。

文本质量：

- 全部活动红线均生成问题；
- 末页不生成翻页动力问题；
- 数值事实来自代码而非 Jev；
- 高风险、灰区、代理冲突升级 LLM；
- runtime failure 不产生 clear；
- `project_proxy` 可消歧，`project_authority` 不可被 Jev 降级；
- 普通 LLM 路径保持原报告契约。

入口选择：

- 第一响应只询问执行选择；
- 选择前不得加载知识、派发子 Agent 或调用 API；
- 普通 LLM 路径不探测 Jev key；
- Jev 路径才检查 key；
- revision run 继承选择；
- 新 root run 重新询问。

### 14.2 集成与回归测试

- 用 fake HTTP transport 和固定 Jev fixture 运行，不在 CI 调真实外部 API。
- 同一 fixture 分别走 `llm` 与 `jev_assisted`，验证顶层质量报告兼容。
- 现有 stage DAG、知识权威、craft benchmark、图片生成和导出测试必须继续通过。
- 插件 contract 测试校验新增 utility skill 已登记，并校验入口选择门位于 workflow 第一位。
- 可选 live smoke test 仅在显式提供 key 时运行，不属于默认测试集合。

## 15. 实施分期

### Phase 1：共享基础设施

- 入口执行选择门。
- `decision_context` 传播。
- 共享 Jev utility skill 与 HTTP client。
- 请求/响应/trace 契约。
- 计时、usage、成本估算和 benchmark case ID。
- fake transport、错误处理和安全测试。

### Phase 2：知识分块相关性筛选

- Markdown 分块和 required 标记。
- `knowledge_relevance` 问题与路由。
- evidence/conflict/excluded-soft 输出。
- Authority revision vector 保持和回归测试。

### Phase 3：红线与逐页文本质量预筛

- `project_proxy` / `project_authority` 分层。
- 全活动红线原子问题。
- 五个逐页文本质量问题。
- LLM 升级包和现有报告合并。
- 中文标注 fixture 与阈值校准工具。

### Phase 4：对比验收

- 同一 benchmark case 分别运行普通 LLM 与 Jev 辅助。
- 合并 Jev trace 与用户从 CC Switch 获得的 LLM usage。
- 输出速度、成本、升级率和分歧报告。
- 逐 operation 决定保持 experimental、调整阈值或标记 calibrated。

## 16. 验收标准

1. 插件新 root workflow 的第一响应必定请求 Jev 选择。
2. 选择普通 LLM 后，当前创作和质检路径无行为回归，也不要求 Jev key。
3. 选择 Jev 后，只有支持的两个 operation 调 Jev；所有生成与最终解释仍由普通 LLM 完成。
4. required 权威知识不可能被 Jev 过滤。
5. Jev/API/解析失败不可能产生 PASS、clear 或知识删除。
6. 所有项目红线都被覆盖；代理命中与最终权威判断明确分层。
7. 每个 operation 产出可审计的模型版本、策略版本、概率、路由、时间、usage 和成本估算。
8. 同一 `benchmark_case_id` 能把两种路径的结果并排比较。
9. trace 和错误输出不包含 API key、Authorization、完整正文或图片数据。
10. 全部现有离线测试通过，新增测试覆盖成功、灰区、失败和显式切换路径。

## 17. 后续扩展点

首轮验证成功后，可按相同运行器扩展：

- `collision_disambiguation`：对字面碰撞候选判断一致、冲突或证据不足。
- `illustration_risk_prefilter`：仅消费文字意图、staging 和已有 `reference_analysis`，筛选风险目录；不读图片、不生成提示词。
- `staging_evidence_check`：判断移动或机位变化是否有文本依据；几何仍由代码投影。
- `revision_impact_classification`：分类反馈影响范围，但不因此跳过 revision 的完整复检。

每个扩展 operation 都必须独立完成：问题设计、中文样本、阈值校准、失败策略和验收；不能因为共享运行器已经存在就默认安全启用。

## 18. 关键设计决定摘要

- 用户先选路径，再执行插件任何实质工作。
- 对用户呈现的是“普通 LLM”与“Jev 辅助”，不是两个等价生成模型。
- Stage DAG 不感知供应商，只携带选择上下文。
- Jev 只做原子概率判断，代码拥有政策和路由。
- 普通 LLM 保留生成、解释性终审和修改建议。
- 知识筛选只过滤 soft chunk；硬约束永久保留。
- 全活动红线都由 Jev 覆盖，正则只提供额外候选证据。
- 逐页质量使用多个 Noul，不制造一个含义模糊的综合分。
- 中文能力未校准前按 experimental 运行并 fail-open/fail-closed 地保护质量。
- 观测以 trace 和 benchmark case 对齐；CC Switch 提供 LLM 侧用量，不绑定其私有数据库。
