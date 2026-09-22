# WorkBuddy 飞书知识库兼容协议

**协议版本：** 1.3.0（2026-09-20 变更：`creation-standards` 升级为双作用域页型，允许系列通用与项目专用并存；项目专用优先）
**适用对象：** WorkBuddy 专家团及后续所有直接读写「绘本创作知识库（AI）」的自动化系统  
**兼容基准：** `picturebook-screenwriter` 插件的 `wiki-ingest`、`feishu-knowledge-store`、`knowledge-loader` 实现  
**生效原则：** 本文档描述的是远端契约。任何实现只要遵守这些格式、状态和并发规则，就可以与 Codex 插件互通；具体使用 lark-cli 还是其他 Feishu API 客户端不是兼容性的必要条件。

## 1. 权威边界

两个飞书知识库的职责固定如下：

| 知识库 | 权限定位 | 允许的操作 |
| --- | --- | --- |
| 绘本创作知识库（原始资料） | 只读输入源 | 编剧和 WorkBuddy 上传、修改原始材料；同步流程只读提取 |
| 绘本创作知识库（AI） | 唯一共享权威知识库 | 按本协议读取、合并、条件写入、登记状态 |

必须遵守：

1. 原始资料库中的内容不得被同步流程创建、更新、删除、移动或修改权限。
2. AI 知识库是唯一共享权威。WorkBuddy 和 Codex 插件都不得把本地缓存、任务临时目录、manifest 或工作区文件当作共享版本基线。
3. 共享同步状态文件是 `99_系统控制台/AI_KB_INDEX_V1`。目标页发布状态和 revision 基线必须从该远端文档读取，并在成功写入后回写到该文档。当前运行的源节点快照、增量状态、manifest 和运行报告只能服务本轮，不得用于恢复、覆盖或替代远端索引。
4. WorkBuddy 生成或修改结构化知识时，必须先在原始资料库产生可追踪的源材料，再按来源版本向量同步到 AI 知识库。不得绕过来源版本记录直接“凭记忆”写入权威页。
5. 人工编辑优先于 AI 内容。人工新增内容必须保留，人工删除内容不得被恢复。
6. 推荐复用本插件的 `wiki-ingest` 与 `feishu-knowledge-store`。如果 WorkBuddy 自行实现，必须完整实现本文的机器契约。
3. WorkBuddy 生成或修改结构化知识时，必须先在原始资料库产生可追踪的源材料，再按来源版本向量同步到 AI 知识库。不得绕过来源版本记录直接“凭记忆”写入权威页。
4. 人工编辑优先于 AI 内容。人工新增内容必须保留，人工删除内容不得被恢复。
5. 推荐复用本插件的 `wiki-ingest` 与 `feishu-knowledge-store`。如果 WorkBuddy 自行实现，必须完整实现本文的机器契约。

## 2. 目标树与逻辑身份

AI 知识库的固定目录结构：

```text
00_使用说明
  └─ AI知识库编辑说明
01_知识内容
  └─ {series_id}/{project_id}/{page_type}
02_导航与日志
  ├─ 知识导航索引
  └─ 同步日志
99_系统控制台
  ├─ 同步索引
  ├─ 同步锁
  └─ 冲突待处理
```

`01_知识内容` 下的 `{series_id}/{project_id}/{page_type}` 表示逻辑归属层级，不是要在 Feishu 中创建的两级物理容器。知识页物理平铺在 `01_知识内容` 的直系子节点中，页面显示标题使用完整逻辑键。

下表中的中文页面标题是面向读者的称呼，不要求作为 Feishu 节点显示标题。逻辑键才是唯一身份；显示标题不参与主键、去重或合并判定。

### 2.1 逻辑键

每个知识页的唯一身份是：

```text
{series_id}/{project_id}/{page_type}
```

规则：

1. 逻辑键必须正好三段，每段都是非空字符串，且不得包含 `/`。
2. 第三段必须等于 `page_type`。
3. 页面显示标题可以变化，但不得作为主键、去重键或合并键。
4. 系列通用知识使用稳定的 `project_id`，推荐固定为 `common`，例如 `海外绘本/common/ip-overview`。
5. 逻辑键全局唯一。创建新页前必须读取同步索引确认没有相同键；索引损坏时先走恢复流程，不得直接创建。

### 2.2 页面类型

新内容应使用以下规范 `page_type`：

| 范围 | page_type | 页面标题 |
| --- | --- | --- |
| 系列通用 | `ip-overview` | IP 总览 |
| 系列通用 | `quality-rubric` | 质量评级标准 |
| 系列通用 | `market-research` | 市场调研 |
| 双作用域 | `creation-standards` | 创作规范 |
| 项目知识 | `worldview` | 世界观 |
| 项目知识 | `characters` | 角色人设 |
| 项目知识 | `content-spec` | 内容规格 |
| 项目知识 | `corrections` | 纠正登记册 |
| 项目知识 | `creative-feature-ledger` | 创意特征台账 |
| 项目知识 | `golden-sentence-registry` | 金句登记册 |
| 项目知识 | `prop-registry` | 道具登记册 |
| 项目知识 | `story-fingerprint-spec` | 选题指纹与门禁规格 |
| 项目知识 | `references` | 参考索引 |

当前解析器还接受若干历史别名，但 WorkBuddy 不得新建历史别名页面。遇到旧页面时，先保持原逻辑键，由人工确认后再迁移。

**双作用域页型规则：**

1. `creation-standards` 可同时存在于 `{series}/common/creation-standards`（系列通用创作规范）和
   `{series}/{project_id}/creation-standards`（项目专用创作规范）。
2. 当项目专用创作规范与系列通用创作规范对同一事项给出不同要求时，**以项目专用为准**。
3. 系列通用与项目专用的 `creation-standards` 是两张独立页面，各自维护各自的来源版本向量与 revision，
   互不覆盖。
4. 不存在 `creation-standards` 专用页型的系列（即没有 `{series}/common/creation-standards`）时，
   项目专用页面仍然合法。

## 3. 知识页格式

远端知识页是飞书原生 `docx`，内容以 Markdown 传输。页面由两部分组成：

1. 读者正文：给编剧和 WorkBuddy 消费的知识内容。
2. 系统元数据：固定在页面末尾，供同步、恢复和审计使用。

不得把 `wiki-ingest` 候选文件的 YAML frontmatter 原样复制到远端页面。候选 frontmatter 只属于任务局部 staging，远端权威页只保留读者正文和系统元数据。

### 3.1 正文要求

1. 每个结构性事实必须带中文来源引用，例如 `> 来源：{原始资料标题}`。
2. 来自 xlsx 的结构化数据必须保持 Markdown 表格，行、列、值不得丢失或改写。
3. 模板要求的 `<!-- machine-data: ... -->` YAML 块必须与人读表格同步维护。
4. 多个原始材料冲突时登记到对应项目的纠正登记册，不得自行裁决哪一方正确。
5. 正文不得包含凭空补全的 token、revision、状态或来源。

### 3.2 系统元数据

每页末尾必须固定追加：

````markdown
{读者正文}

## 系统元数据（请勿编辑）
```json
{"key":"海外绘本/小老鼠迈尔斯/worldview","last_ai_revision_id":42,"page_type":"worldview","schema_version":1,"source_node_tokens":["node-token-a","node-token-b"],"source_revisions":{"node-token-a":"17","node-token-b":"28"}}
```
````

严格规则：

1. `## 系统元数据（请勿编辑）` 在整页中只能出现一次。
2. 该标题必须是最后一个独立章节，前面保留一个空行，后面只能是一个可选空行加 `json` 代码块。
3. JSON 必须是对象，字段必须且只能是：
   - `schema_version`
   - `key`
   - `page_type`
   - `source_node_tokens`
   - `source_revisions`
   - `last_ai_revision_id`
4. `schema_version` 必须为整数 `1`。
5. `source_node_tokens` 必须是非空字符串数组，且不得重复。
6. `source_revisions` 必须是非空对象；键集合必须与 `source_node_tokens` 完全一致；值必须是原始资料节点修订号字符串。
7. `last_ai_revision_id` 必须是非负整数，表示该目标页最后一次成功 AI 写入后的目标 docx revision。
   - 首次发布新页面时，最终页尾不能写 `0`。创建请求可以用 `0` 占位；实现必须在创建后重读页面、执行元数据修正写入，并把最终页尾改为本次修正写入完成后的真实 revision。
   - Feishu 的 create/overwrite revision 推进量是实现行为而非协议常量；不得预设 create 从 `1` 起计，也不得假设 overwrite 恒推进 `+1`。
   - 后续 AI 更新时，页尾元数据写入本次更新预期产生的新 revision；若实际返回值不同，必须用本轮实际推进量重新学习并重写，直到元数据值等于实际返回 revision。索引中的 `last_ai_revision_id` 与 `last_seen_revision_id` 都写入实际返回的新 revision。
8. 写入时推荐使用规范化 JSON：`sort_keys=true`、`separators=(",", ":")`、`ensure_ascii=false`。这能让不同系统写出字节一致的元数据。
9. 不得在系统元数据中新增备注、作者、时间或其他字段。需要这些信息时写入同步日志。

## 4. 状态记录契约

### 4.1 版本字段分工

| 字段 | 类型 | 含义 | 更新者 |
| --- | --- | --- | --- |
| `source_revisions` | `dict[string, string]` | 生成该页内容的原始资料节点 token 到修订号的映射 | 页面成功发布后由同步系统更新 |
| `last_ai_revision_id` | `int` | 目标页最后一次成功 AI 写入后的目标 revision | WorkBuddy 或 Codex 插件成功写入后更新 |
| `last_seen_revision_id` | `int` | 同步时观察到的目标页当前 revision | 每次同步读取目标页后更新；仅人工编辑时它变化而 `last_ai_revision_id` 不变 |

关键区别：

1. 原始资料修订号统一用字符串。
2. 目标 docx revision 统一用非负整数。
3. 人工编辑目标页后，只应观察到 `last_seen_revision_id` 前进，不得伪造 `last_ai_revision_id` 前进。
4. WorkBuddy 或插件成功条件更新目标页后，`last_ai_revision_id` 与 `last_seen_revision_id` 应同时更新为返回的新 revision。
5. `revision-id` 只是随请求透传的审计参数，飞书服务端不会用它做乐观并发控制。协议中的互斥不依赖服务端 CAS，而依赖远端锁加写后回读校验。
6. 如果更新返回 `warnings`、`partial_success`、非成功 result，或无法取到新 revision，不得更新为 published 状态，也不得记录成功 revision。

### 4.2 状态枚举

| status | 语义 | 允许的设置条件 |
| --- | --- | --- |
| `published` | 页面和索引状态一致，可正常消费 | 条件写入成功、响应完整无警告、索引已成功更新 |
| `needs_review` | 页面可读，但存在未处理冲突或不确定状态 | 人工与来源语义冲突、页面含资源/评论/未知块、更新 partial、响应有 warnings、revision 冲突重试后仍失败、索引与页面无法对齐 |
| `archived` | 逻辑键已废弃，但保留审计记录 | 用户明确确认废弃；不得因临时失败或找不到源自动设置 |

状态必须写入同步索引，而不是只写在页面正文或本地文件中。`needs_review` 页面仍可被读取，但调用方必须提示“存在未处理冲突”，不得当作已完全验证的定稿知识。

## 5. 同步索引格式

`99_系统控制台/同步索引` 是共享状态清单。正文必须严格为：

````markdown
# AI_KB_INDEX_V1
```json
{"entries":[{"doc_token":"doc-token","key":"海外绘本/小老鼠迈尔斯/worldview","last_ai_revision_id":42,"last_seen_revision_id":45,"source_revisions":{"node-token-a":"17"},"status":"published","wiki_node_token":"wiki-node-token"}],"schema_version":1}
```
````

解析规则：

1. 正文必须以 `# AI_KB_INDEX_V1\n```json\n` 开头，以 `\n```\n` 结尾，不能有额外文字、表格或备注。
2. 顶层 JSON 对象字段必须且只能是 `schema_version` 和 `entries`。
3. `schema_version` 必须为整数 `1`。
4. `entries` 必须是数组，且逻辑键不得重复。
5. 每条索引记录字段必须且只能是：
   - `key`
   - `doc_token`
   - `wiki_node_token`
   - `source_revisions`
   - `last_ai_revision_id`
   - `last_seen_revision_id`
   - `status`
6. `doc_token` 与 `wiki_node_token` 必须是非空字符串，并指向同一个可读取的目标页。
7. `source_revisions` 与状态字段遵循第 4 节规则。
8. 索引更新必须使用读取时的当前 revision 作为条件更新前提。
9. 索引无法解析、字段不完整、token 不可读或出现重复键时，必须停止写入并进入恢复流程，不得用本地缓存覆盖远端索引。

## 6. 同步锁格式

`99_系统控制台/同步锁` 是远端租约。正文必须严格为：

````markdown
# AI_KB_LOCK_V1
```json
{"expires_at":"2026-09-17T08:45:00Z","holder":"workbuddy-user","run_id":"32-char-run-id","schema_version":1,"started_at":"2026-09-17T08:00:00Z"}
```
````

空闲锁为：

````markdown
# AI_KB_LOCK_V1
```json
{"expires_at":null,"holder":null,"run_id":null,"schema_version":1,"started_at":null}
```
````

规则：

1. 顶层字段必须且只能是 `schema_version`、`run_id`、`holder`、`started_at`、`expires_at`。
2. 时间使用 UTC ISO 8601，推荐以 `Z` 结尾。
3. 租约有效期默认 45 分钟，允许 15 到 120 分钟。
4. 锁活跃的条件是 `run_id` 非空且 `expires_at` 大于当前 UTC 时间。
5. 获取锁必须先读取锁文档，再以读取到的 revision 作为审计参数写入新租约，然后立即重读锁文档并核对 `run_id` 与 `holder`。只有回读结果确实是本方租约时，才认为获锁成功。
6. 只有锁的持有者可以续租或释放。释放时把四个租约字段置为 `null`，释放后也必须回读确认锁已空闲。
7. 长任务必须在阶段边界续租。异常结束时也要尽力释放，但不能释放他人租约。
8. WorkBuddy 不得直接手工编辑锁文档。锁只能通过上述获取、续租、释放协议更新。
9. 目标 Wiki 中必须只有一个 `AI_KB_LOCK_V1` 节点。初始化或治理时发现重复锁文档，必须先保留一个主锁并把其他锁文档改名或归档，之后所有系统必须引用同一个锁文档 token。
   - “只有一个”的检查作用域是 `99_系统控制台` 的直系子节点。历史测试区或归档容器中的同名节点不参与活跃锁判定。
   - 归档重复锁时仍建议加上明确的归档标题前缀，避免人工查看时混淆。

## 7. 写入与同步流程

WorkBuddy 每次写入 AI 知识库必须按以下顺序执行：

1. **只读预检**
   - 验证当前用户身份认证成功。
   - 验证对原始资料库根节点有读取权限。
   - 验证对 AI 知识库根节点有读取和写入权限。
   - 任一预检失败时停止，不得创建替代知识库。

2. **获取远端锁**
   - 按第 6 节获取租约。
   - 锁被持有时返回持有者和到期时间，不得继续写入。

3. **读取并验证索引**
   - 按第 5 节解析。
   - 所有写入决策必须基于远端索引和远端页面，不得基于本地缓存。

4. **读取目标页当前状态**
   - 获取当前 `revision_id`、正文和系统元数据。
   - 校验元数据 schema、逻辑键、来源 token 和 revision。
   - 页面含资源、评论或未知块时标记 `needs_review`，不得自动覆盖。

5. **生成候选与合并决策**
   - 从原始资料库读取增量，保留来源 token 和来源修订号。
   - 与索引中的 `last_ai_revision_id` 历史版、目标当前版、新候选版做三方合并。
   - 人工优先规则见第 8 节。

6. **条件更新知识页**
   - 使用 `docs +update` 且带当前 `revision_id`。
   - 更新成功后完整检查 `warnings`、`result`、`partial_success` 和返回的新 `revision_id`。
   - 写入后必须回读目标页，确认正文与系统元数据符合预期，且 `last_ai_revision_id` 与实际返回的 revision 一致。
   - 只有全部正常才进入索引更新。

7. **更新同步索引**
   - 用新的目标 revision 和来源版本向量更新对应条目。
   - 索引更新时先读取当前 revision，写入后回读并比对 `entries`。
   - 回读结果与预期不一致时停止写入，并把相关条目标为 `needs_review`。
   - 索引更新失败时不得报告完全成功，应把相关条目标为 `needs_review` 并报告实际状态。

8. **更新导航与日志**
   - 在持锁期间更新 `02_导航与日志/知识导航索引`。
   - `同步日志` 仅追加，不重写历史。
   - 记录操作者、时间、逻辑键、发布/保留/入队/失败数量、目标页更新前后 revision。

9. **释放锁**
   - 无论成功、失败或异常，都要在 finally 语义中释放自己的租约。

10. **输出报告**
    - 报告必须包含发布、保留、入队、失败、重试数量。
    - 列出涉及的逻辑键和页面标题。
    - 列出冲突队列新增记录。
    - 列出每个页面更新前后的目标 revision。

## 8. 人工优先合并规则

| 情况 | 必须行为 |
| --- | --- |
| 原始资料无变化 | 保留当前目标页，不改写 |
| 仅原始资料变化 | 发布候选版，保留人工未冲突内容 |
| 仅人工编辑 | 保留当前目标页，并把人工版作为下一轮基线 |
| 两者变化且无语义冲突 | 合并：保留人工变化，纳入来源新增内容 |
| 两者变化且语义冲突 | 人工内容胜出；不覆盖页面，登记冲突 |
| 条件更新遇到 revision 冲突 | 重读最新目标页并重新合并；仍不安全则入队 |

合并结果必须满足：

1. 人工新增行不得丢失。
2. 人工删除行不得恢复。
3. 来源新增内容不得遗漏，除非它与人工内容冲突并已入队。
4. 合并后的页面必须仍符合第 3 节格式。

## 9. 冲突队列

`99_系统控制台/冲突待处理` 是仅追加文档。当前兼容格式为每条记录一行：

```text
[{logical_key}] {reason}
```

规则：

1. 必须在持有同步锁时追加。
2. 追加前读取当前 revision，并用该 revision 条件更新。
3. 追加后必须回读，确认新增记录存在且原有记录未丢失。
4. 不得删除、改写或重排已有记录。
5. 不得把同一末尾记录重复追加。
6. 冲突详情、处理建议和审计信息写入同步日志或来源材料，不得改坏冲突队列的简单行格式。

解析规则：

1. 文档标题、空行和不以 `[` 开头的说明性框架行允许存在；解析方必须忽略这些非记录行。
2. 以 `[` 开头的行是记录行，必须符合 `[逻辑键] 原因`；解析失败时停止写入并报告。
3. 空队列可以只包含标题（可带空行），但带有说明性占位行也属于当前兼容格式。

## 10. Feishu 命令约束

如果使用 lark-cli，目标页操作必须使用：

```text
docs +fetch --as user --doc {doc_token} --doc-format markdown
docs +update --as user --doc {doc_token} --command overwrite --doc-format markdown --revision-id {current_revision} --content {content}
```

新建 docx 使用：

```text
docs +create --as user --parent-token {wiki_node_token} --title {title} --doc-format markdown --content {content}
```

禁止：

1. 禁止对共享权威页面使用无 revision 前提的 `markdown +overwrite`。
2. 禁止把 `--revision-id` 当作服务端 CAS 使用。飞书 1.0.95 / 1.0.96 实测不会拒绝过期 revision；写入成功后必须回读校验。
3. 禁止使用 `docs +get`。lark-cli 1.0.95 与 1.0.96 均不存在该命令，文档读取命令是 `docs +fetch`。
4. 禁止把目标 revision 当字符串写入索引或页面元数据。
5. 禁止把原始资料 revision 当整数写入 `source_revisions`。
6. 禁止只看 HTTP 或 CLI 成功码就判定发布成功；必须检查完整 JSON 结果，并核对写后回读内容。

## 11. 禁止事项

1. 禁止写入或修改原始资料库。
2. 禁止把本地 staging、manifest、缓存、数据库或工作区文件当作共享权威状态。
3. 禁止绕过同步锁直接更新知识页、索引、导航或日志。
4. 禁止绕过 `revision_id` 条件更新。
5. 禁止修改或删除页尾系统元数据，除非处于经过验证的迁移流程。
6. 禁止用显示标题、URL、文件路径或本地路径替代逻辑键。
7. 禁止在状态未真实成功时写入 `published`。
8. 禁止静默吞掉冲突、partial success、warnings、权限错误或索引损坏。
9. 禁止恢复人工删除的内容。
10. 禁止在索引中新增任意字段或用自然语言替代 JSON。
11. 禁止用 WorkBuddy 私有状态文件替代远端同步索引。
12. 禁止用当前运行的 `nodes_snapshot.json`、`delta_state.json`、`_manifest.json`、`sync_report.json` 或知识检索缓存恢复、替代或重建远端同步索引。
13. 禁止为了绕开解析错误而重建一个新逻辑键、新页面或新知识库。

## 12. WorkBuddy 上线前检查清单

- [ ] 原始资料上传只进入「绘本创作知识库（原始资料）」。
- [ ] AI 知识库写入前完成认证、源读、目标读写预检。
- [ ] 所有写入都在远端租约保护下执行。
- [ ] 所有页面更新都带当前目标 `revision-id`，但不得把它当作服务端 CAS；写入后必须回读校验。
- [ ] 初始化或治理时确认 `99_系统控制台` 下只有一个 `AI_KB_LOCK_V1` 节点。
- [ ] 锁获取和释放都执行写后回读，并核对 `run_id`、`holder` 与预期状态。
- [ ] 页尾系统元数据字段、类型和 JSON 形状完全一致。
- [ ] `source_node_tokens` 与 `source_revisions` 一一对应。
- [ ] 原始资料 revision 为字符串，目标 docx revision 为非负整数。
- [ ] 首次发布页面的页尾 `last_ai_revision_id` 经修正闭环后等于最终写入的真实 revision，不得写 `0`。
- [ ] 同步索引是唯一状态来源，且字段集合完全一致。
- [ ] 成功写入同时更新页面元数据与索引状态。
- [ ] 索引与冲突队列写入后回读比对，结果不一致时停止并进入 `needs_review`。
- [ ] 冲突、partial、warning、索引失败时设置 `needs_review` 并报告。
- [ ] 冲突队列只追加兼容行。
- [ ] 人工新增保留、人工删除不恢复。
- [ ] 结束时释放自己的锁，并输出完整中文报告。

## 13. 实现依据

本协议依据以下当前实现和设计：

- `plugins/picturebook-screenwriter/skills/wiki-ingest/SKILL.md`
- `plugins/picturebook-screenwriter/skills/wiki-ingest/references/structured-output-templates.md`
- `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/SKILL.md`
- `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/page_codec.py`
- `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/control_plane.py`
- `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/publisher.py`
- `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/merge_protocol.py`
- `docs/superpowers/specs/2026-09-17-feishu-authoritative-knowledge-base-design.md`
