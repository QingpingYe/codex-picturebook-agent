# Codex KB-AI 来源状态、候选时效与源准入设计

> 日期：2026-09-29｜状态：待用户审阅｜批次：2/4
>
> 本文规定 `source_edit_times` 的生产与远端化判据，并把 WorkBuddy 交接单中的 `AI_KB_SOURCE_ADMISSION_V1` 子树排除 P0 前移到本批。本文不触发飞书写入，不解除 Codex 与 WorkBuddy 并发操作同一 KB-AI 的禁令。

## 1. 目标与边界

本批同时解决三个相互依赖的问题：

1. 把源节点编辑时间从源快照送入候选、页面页尾和远端索引。
2. 用远端 `AI_KB_INDEX_V1` 的版本证据替代跨运行本地 state，决定源节点是否需要重新读取。
3. 读取远端 `AI_KB_SOURCE_ADMISSION_V1`，默认纳入未裁定节点，只对被显式 `exclude` 的节点及其全部后代停止摄入和发布。

本批不改变第一批已经冻结的内容权威规则、正文比较算法、唯一发布路径、`needs_review` 语义或逐页报告字段。它不读取历史版，不恢复冲突队列，不自动归档源删除对应的目标页，也不执行真实飞书写入。

`source_edit_times` 的远端化只替代“源是否变化”的本地判据。候选正文仍是页面内容权威；远端索引仍是目标同步状态权威；本地缓存仍只是加速和一致性证据，不能成为共享基线。

## 2. 现状与职责边界

| 产物 | 权威 | 本批前状态 | 本批后允许用途 |
| --- | --- | --- | --- |
| `nodes_snapshot.json` | 本轮源树事实 | 已有 `edit_time_ms`，但候选链未消费 | 记录本轮看到的节点和编辑时间 |
| `source_baseline.json` | 远端只读投影 | 不存在 | 提供远端索引版本证据和准入快照，不作为发布状态写入源 |
| 候选 frontmatter | 本轮候选提议 | 只有 `source_revision_parts` | 新增 `source_edit_time_parts`，与 token/revision 顺序一致 |
| `AI_KB_INDEX_V1` | 唯一远端同步状态权威 | 已有可选 `source_edit_times` | 提供来源版本基线和页面同步状态 |
| 目标页页尾 | 页面一致性证据 | 已有可选 `source_edit_times` | 记录上次写页时的来源时间映射 |
| `AI_KB_SOURCE_ADMISSION_V1` | 远端只读准入策略 | 本仓无读取路径 | 决定源节点自身及子树是否进入摄入流程 |
| `delta_state.json` | 跨运行本地状态 | `check_delta.py` 仍读取并 finalize | 退役；不得再决定 skip/process |
| `cache/` | 本地加速缓存 | 用于哈希校验 | 可继续作为 `unchanged` 的附加证据，不得提供权限或状态 |

当前 `check_delta.py` 的 `classify(snapshot, state, cache_dir, ...)`、`merge_state(...)`、`--state`、`--write-state`、`--hash-map` 和 `--revision-map` 是旧边界。最终接口必须改成远端只读基线与纯比较，不再存在跨运行 finalize 写回。

## 3. 数据模型与接口

### 3.1 源快照

`wiki-ingest` 继续生成 `nodes_snapshot.json`。每个内容节点必须有：

- `node_token`：非空字符串。
- `parent_node_token`：根节点允许为空，其余节点必须指向快照中的节点。
- `obj_type`：原类型。
- `edit_time_ms`：正整数毫秒时间戳。
- 可用的 `revision_id`；部分文件类型允许缺失，但必须保留原值语义。

`edit_time_ms` 缺失、布尔值、非整数或不大于零时，该节点不得被判 `unchanged`；它进入 `unknown`，按 fail-safe 重新读取或由准入规则排除。

### 3.2 候选时效字段

候选 frontmatter 新增：

```yaml
source_edit_time_parts:
  - 1759000000000
  - 1759000000100
```

规则：

- 与 `source_node_tokens`、`source_revision_parts` 长度一致、顺序一致。
- 每个值都是正整数毫秒时间戳；布尔值、字符串、零和负数非法。
- `parse_candidate()` 将它验证为 `metadata["source_edit_times"]`。
- 一个 token 在候选内只能出现一次。
- 缺字段或长度不一致时，候选不能进入发布路径。

`generate_entries.py --validate-only` 必须要求 `--nodes <nodes_snapshot.json>`。逐候选逐 token 比较时：

- 候选时间与快照 `edit_time_ms` 不等：`FAIL`。
- 快照缺少可用编辑时间或候选缺少可验证项：`WARN`，不得标记为通过。
- `--nodes` 缺失、文件不可读或 JSON 无效：退出码 `2`。
- 生成 manifest 的成功路径必须把同一个有序时间向量写入候选审计数据。

### 3.3 页面与索引时间映射

页面页尾和索引条目的 `source_edit_times` 规则沿用第一批 schema：

- 键集分别与 `source_node_tokens` 或 `source_revisions` 完全一致。
- 值为正整数毫秒；缺字段或索引中的 `null` 表示旧条目或时间基线缺失。
- 实际写页时，新时间向量随候选写入页尾；正文保留且来源集合不变时只允许按第一批规则刷新索引时间。
- 来源集合变化而正文相同，仍按第一批规则保留旧来源向量和时间映射并在报告披露拓扑待处理；本批必须给出不拼接过期映射的收敛方案后才能在生产同步中依赖它。

### 3.4 远端只读基线

`feishu-knowledge-store` 新增只读命令：

```text
store_cli.py source-baseline --out <run_id>/source_baseline.json
```

它满足：

- 读取并严格验证 `AI_KB_INDEX_V1`。
- 读取 `AI_KB_SOURCE_ADMISSION_V1`；页面缺失时输出空准入策略。
- 不获取或修改远端锁、索引、页面或准入页。
- 输出包含索引控制 revision、规范化索引条目和已验证准入快照的 JSON。
- 索引缺失或损坏时命令失败；准入页损坏时命令失败。
- 输出是只读事实投影，不能被 `wiki-ingest` 当作写入授权。

`check_delta.py` 最终接口改为：

```text
check_delta.py --nodes <snapshot.json> --source-baseline <source_baseline.json>
  [--cache-dir <dir>] [--force-full] [--only <tokens>] [--out <plan.json>]
```

不再接受 `--state`、`--write-state`、`--hash-map` 或 `--revision-map`，也不再提供 finalize 模式。

### 3.5 准入策略接口

新增只读 `SourceAdmission` 组件，不从 `ControlPlane` 借用写入职责：

```text
load_source_admission(payload) -> AdmissionPolicy
resolve_source_admission(policy, snapshot) -> AdmissionResult
```

`AdmissionPolicy` 只保存已验证的六字段条目；字段名和精确约束来自 WorkBuddy `AI_KB_SOURCE_ADMISSION_V1` 契约。实现前必须先通过授权的只读命令取得真实样例并固化为离线 fixture；不得猜测字段名，不得接受未知字段，不得把解析失败降级为空策略。

`AdmissionResult` 至少包含：

- `excluded_tokens`：被显式排除的 token 及其全部后代。
- `excluded_containers`：被排除且不再遍历的容器 token。
- `included_tokens`：未裁定默认纳入或被显式 `admit` 的节点。
- `active_decisions`：按契约确定的最新有效决定；同一 token 的决定顺序不明确时判为冲突。

## 4. 判定语义

### 4.1 源版本基线

`check_delta.py` 从远端索引投影构造 token 级基线：

- token 出现在一个或多个索引条目中时，收集其 `source_edit_times` 和 `source_revisions`。
- 多处证据一致：形成该 token 的远端版本基线。
- 任一索引条目缺少该 token 的时间或 revision，或不同条目给出冲突值时：该 token 基线为 `unknown`，不得 skip。
- token 不在任何索引条目中，但当前快照存在：判 `first_run` 或 `new`，必须处理。
- token 出现在远端索引任一条目中，但当前源快照不存在：判 `deleted` 并只进入报告；不得自动归档目标页，也不得从本地 state 推断删除。
- `force_full`：所有未排除节点判 `changed`，但仍先应用准入排除。

仅当以下证据全部成立时才能判 `unchanged`：

1. 节点未被准入排除。
2. 快照 `edit_time_ms` 与远端基线一致。
3. 对 `docx`/`wiki` 节点，快照可用 revision 与远端基线一致；revision 为缺失或 `N/A` 时不得跳过。对没有 revision 语义的文件节点，以编辑时间和缓存哈希作为正向证据。
4. 本地缓存存在且哈希与记录的 `feishu_hash` 一致。

缓存缺失、哈希不符、缓存目录缺失或 revision 不可确认时，结果降级为 `changed`，原因是“缺少正向 skip 证据”。这允许重读，不会漏掉变化源。

### 4.2 准入判定

- 未裁定的节点默认 `admit`。
- 只有 `decision == "exclude"` 产生排除效果。
- `admit` 只用于撤销先前排除；它不能绕过当前有效的最新 `exclude`。
- 被排除 token 自身、全部后代和后代容器都判 `excluded`，不得下载正文、扫描外链、生成候选或发布。
- 排除集合为空时，不要求祖先链完整。
- 排除集合非空且祖先链缺父节点、父节点不在快照中或形成环时，整轮停止并逐行列出问题 token。
- `--only` 命中 `excluded` token 时拒绝执行；未裁定 token 不拒绝。
- 远端准入页缺失等同空排除；页面存在但标题、`schema_version`、六字段结构、决策枚举或决定顺序无效时硬失败。
- 不创建、不修改、不删除远端准入页。

### 4.3 本地 state 退役

在远端基线实现并通过回归后：

- 从运行目录布局、`wiki-ingest/SKILL.md`、提取参考和测试中移除 `delta_state.json` 的跨运行含义。
- 删除 `merge_state`、finalize 路径和四个对应 CLI 参数。
- 保留本轮快照、staging、可抛弃缓存和 plan 输出原子写。
- 任何仍读取 `delta_state.json` 的调用方必须在同一批迁移或明确删除；不能留下只写不读或只读不写的半迁移状态。

## 5. 失败语义

| 失败 | 行为 |
| --- | --- |
| 源快照节点 `edit_time_ms` 缺失/非法 | 该节点 `unknown` 并重新读取；不阻断其他节点 |
| 远端索引缺失或损坏 | `source-baseline` 命令失败；源摄入不得声称有可靠 skip 基线 |
| 远端索引条目时间缺失/冲突 | 受影响 token 基线 `unknown`，必须处理 |
| 准入页缺失 | 空排除策略 |
| 准入页 schema/决定无效 | 停止整轮，列出首条错误 |
| 排除祖先链断裂或成环 | 停止整轮，逐行列出 token |
| 缓存缺失/哈希失败 | 判 `changed`，重新读取 |
| 候选时间与快照不一致 | 机械校验 `FAIL`，不得发布 |
| `--only` 命中排除 token | 拒绝执行并列出 token |
| 目标页发布失败 | 交由第一批 `needs_review` 和报告语义处理，不写入准入页 |

## 6. 跨批接口

- 第一批 `source_edit_times` 的 schema 和 preserve 刷新规则保持不变。
- 第二批把远端 `source_edit_times` 作为来源增量基线；`source_revisions` 仍用于页面元数据和 corroboration，不能替代正文比较。
- 第三批消费准入结果和 `NEW_SOURCES` 审计计数；不得重新定义默认纳入或子树排除。
- 第四批只在双方行为已验证后更新兼容协议和解除门槛。
- 在第二、三批完成且跨侧只读验收通过前，不解除并发禁令。

## 7. 验收门槛

离线测试必须覆盖：

1. 快照、候选 token/revision/time 三组列表的顺序、长度、正整数和唯一性。
2. `source_edit_time_parts` 缺失、非法或不匹配时不得通过 B1 校验。
3. 索引无 token、时间缺失/冲突、revision 不一致、缓存缺失和哈希不符时正确降级为处理。
4. 时间、revision 和缓存全部匹配时才判 `unchanged`。
5. 远端索引有来源 token、快照缺失时判 `deleted`，但不自动归档目标页。
6. `source-baseline` 对索引和准入页只读，绝不调用任何写命令；准入页缺失为空策略，损坏为硬失败。
7. 未裁定默认纳入、叶子排除、容器子树排除、未来新增后代自动继承排除、悬断祖先链和环。
8. `admit` 撤销、多个决定的确定性、未知字段和错误 `schema_version` 的失败语义。
9. `--only` 对 excluded、未裁定和正常 token 的行为。
10. `delta_state.json`、finalize、local state CLI 参数和 `merge_state` 已无运行时消费者。
11. 技能文档、模板、帮助文字和测试不再把本地 state 描述为跨轮基线。

只读远端验收可验证真实索引与准入页解析；不得执行任何远端写入。远端样例固化并测试通过后，才允许编写依赖真实 schema 的实现代码。