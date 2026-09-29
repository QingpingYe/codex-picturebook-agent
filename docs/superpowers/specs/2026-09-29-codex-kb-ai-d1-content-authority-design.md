# Codex KB-AI 内容权威与发布契约设计

> 日期：2026-09-29｜状态：已实施（离线验收通过；远端验收未执行）｜批次：1/4
>
> 本文只规定第一批的目标、接口和验收；不触发飞书写入。产品依据为 WorkBuddy `2026-09-22-D1-Codex-交接单.md`，其中比较算法采用用户在 2026-09-29 本会话的新决定：“以内容本身为准，标记符号不参与内容是否变化的判断”。历史需求归属见 WorkBuddy `2026-09-29-Codex-需求溯源清单.md`。

## 1. 目标与边界

KB-AI 页面内容由 AI 候选页决定。已有页不再因为来源修订号未变而直接保留，也不再读取历史版做人工优先三方合并。读到当前页后，对候选与当前页的**正文内容**做相同规则的比较：内容相同则不写页，内容不同则发布候选并覆盖旧正文。人工直接修改 KB-AI 页面的增删改不再有粘性；实际成功覆盖时，报告必须披露对应逻辑键。

这次一并退役冲突队列的代码入口、队列报告和“`needs_review` 等于待处理冲突”的旧口径。远端已有 `AI_KB_CONFLICT_QUEUE_V1` 节点保留原位，不删除、不改写。

本批不改变源节点纳入/排除策略，不切换 `check_delta` 的本地判据，也不退役本地 state/finalize；它们属于后续批次。第一批完成后仍不把 Codex 与 WorkBuddy 对同一 KB-AI 的并发同步视为已放行，须待源侧批次与跨侧验收完成。

## 2. 本仓现状与设计入口

- `SyncRunner.publish()` 是 `store_cli publish` 的执行入口；`target_state.classify_target()` 目前按 `source_revisions` 相同直接 `preserve`；`SyncRunner._update_existing()` 读取 `last_ai_revision_id` 对应历史版并调用 `merge_protocol.classify()`。
- `sync_knowledge.SyncService` 是另一条以 `MergeDecision` 和队列为核心的旧路径；技能文档和测试仍引用它。本批以 `SyncRunner` 作为唯一发布路径，退役 `SyncService`/`merge_protocol` 的对外使用与测试，不留下可继续执行旧语义的入口。
- `Publisher` 仍强制定位/初始化冲突页；`store_cli` 仍暴露 `conflict-list`、`conflict-append`；`contract_lint` 仍要求 `conflict.md` fixture。
- 当前 `SyncRunner.SyncReport` 使用顶层计数，尚无 WorkBuddy 的嵌套 `counts` 结构。本批保持 Codex 报告的顶层形态，同时同步 D1 的字段语义；本地报告不是远端 KB-AI 控制页 schema。
- 当前索引/页尾解析器严格只接受六个页尾字段及既有索引字段。为能读取 WorkBuddy 已使用的远端页，本批接受**可选** `source_edit_times`，但其生产、源侧增量判定和本地 state 退役留给第二批。

## 3. 页级判定与标记不敏感比较

每个候选按以下顺序处理：持有远端租约 → 读取并验证索引 → 读取目标页当前 revision、正文和页尾元数据 → 先检查是否满足第 4 节的受限恢复条件，再做常规判定 → 按当前 revision 写页 → 写后回读 → 更新索引并回读。缺索引条目但远端已有同逻辑键页时停止该页的自动写入，不能按“首次发布”覆盖或收编它；首次创建后若索引未写成，也遵循这一规则，须人工核实。

| 条件 | 动作 |
| --- | --- |
| 索引无键，目标树中也无同键页 | 首次发布；经创建后页尾 revision 修正和回读，再写索引 |
| 已有页的正文内容与候选相同 | `preserve`；目标页零写入，索引可记录真实观察到的 `last_seen_revision_id` 并按第 4 节刷新同一来源集合的时间基线 |
| 已有页的正文内容与候选不同，页面可安全往返 | 按当前 revision 条件更新为 AI 候选正文；成功回读后更新索引 |
| 页含资源、评论或未知块；元数据/索引不一致且不满足受限恢复；写入结果 partial 或有 warnings | 不声称发布成功；记录 `needs_review` 与失败原因，不写冲突队列 |
| revision 竞态 | 重读当前页并重新判定，限次重试；耗尽则本轮 `failed += 1`、`needs_review`，下轮仍可自然重试 |
| 索引状态为 `archived` | 不自动恢复或发布，报告需要治理处理；不得入旧冲突队列 |

比较仅作用于读者正文，不把页尾系统元数据、来源版本号或飞书 revision 当作“文字是否变化”的替代判据。候选与回读正文使用同一个 `content_signature` 规则；这个规则也用于写后验证正文，元数据仍按各字段语义验证。其中索引的 `source_edit_times` 记录最近一次已摄取源快照，可以在页面正文保留时前进；页尾的同名字段记录上次写页时的值，二者不要求相等。

`content_signature` 忽略 Markdown **排版语法**和飞书 docx 往返已知的排版改写：成对的强调标记、引用/标题等排版前缀、首尾空行及行尾排版空格、自动链接包裹写法、表格的空尾格。代码围栏与行内代码内部不做排版归一；普通链接保留目标 URL，只有 `[URL](URL)` 这类目标与显示文本相同的自动链接包裹可与裸 URL 视为等价。其余文字、标点、代码内容、URL 目标、非空表格单元格及其顺序均保留。`*`、`_`、`#` 等字符若是正文中的字面内容、代码或 URL 一部分，必须保留；不能照搬 WorkBuddy 当前“删除全部 `*`/`_`”的实现。完全相同的原文片段无需猜测其语法；只有两边确实不同、又无法安全归一的片段才判为内容不同，避免漏掉真实改动。此规则只用于比较，不改写要发布的候选正文。

由此接受一项明确代价：只改加粗、引用样式等纯排版意图，不构成内容变化，可能不触发发布。实际改词、增删文字、改标点或改链接目标，必须触发发布。

## 4. 远端索引、revision 与异常

页面更新必须维持现有租约、当前 revision 前提和写后回读。不能把 `revision-id` 参数误当成飞书服务端可靠的 CAS；回读正文、页尾与 revision 才能确认结果。`last_ai_revision_id` 来自已验证的 AI 写入页尾；索引 `last_seen_revision_id` 取**写后回读**得到的实际目标页 revision，而非仅取写请求响应。两者可以不同，故 `contract_lint` 不再要求它们恒相等；仍须检查页尾与索引的 AI revision 一致，以及 `last_seen_revision_id` 不小于已确认的 AI revision。

为兼容已有远端页和第二批的过渡，本批的 `IndexEntry`、索引解析/渲染、候选/页尾解析与发布过程允许 `source_edit_times` 作为 schema v1 的可选字段。索引条目允许该键缺失或为 `null`（旧条目），有值时必须是与 `source_revisions` 键集一致的正整数毫秒映射；远端页尾允许该键缺失，有值时必须与 `source_node_tokens` 键集一致。候选 frontmatter 的对应字段是 `source_edit_time_parts`，与 `source_node_tokens` 按顺序一一对应，解析后形成映射。

`preserve` 时页面来源版本不更新，索引中的 `source_revisions` 保持旧值。若候选提供有效时间映射且来源 token 集合与旧索引相同，仅刷新索引的 `source_edit_times`，目标页零写入；旧候选无时间字段时保留既有映射。候选时间不得比索引中对应 token 的有效时间更早；检测到回退时，本轮不刷新基线，报告过期候选并停止该页自动发布。若来源 token 集合改变而正文相同，本批保留旧索引来源向量与时间映射并在报告披露来源拓扑待处理，不把不匹配的映射拼接成有效基线；这会导致新来源重复摄取，第二批必须给出自动收敛规则后才可用于生产同步。

真正发布新正文时，候选提供有效时间字段就按候选版本向量写入，但若同 token 的候选时间早于索引有效时间，本轮停止该页发布并报告过期候选。候选缺时间字段、`source_revisions` 与旧索引完全相同时，可沿用旧的有效时间映射；来源向量不同时，页尾省略 `source_edit_times`、索引置 `null`，并报告源时间基线缺失，不能把旧映射与新向量拼接。首次发布且候选缺时间字段也写 `null` 基线并报告降级。本批不据时间戳判断源是否变化，也不要求旧候选补齐它。

revision 冲突或响应 revision 不符时，不能沿用旧的写前 revision 盲目再次覆盖。若请求明确未写成，重新读取当前页并重新判定后限次重试；若请求可能已写入（含响应与回读不一致、partial/warnings），先回读确认真实状态，不确定时停在 `needs_review`，不自动重写。远端租约仍是主要互斥机制；`revision-id` 不是可靠服务端 CAS，因此覆盖披露只能依据**本次写前已观察到**的外部 revision 前进，无法凭它证明读写间没有其他写者。

若页面写入已确认但索引更新失败，或写请求后无法完成页/索引回读，整轮不得报告完全成功，也不得继续把后续页面当作正常发布；报告保留已知的写前/写后 revision 与实际覆盖结果，未知值明确记为 `null`。下一轮持锁后可做**受限的远端恢复**：仅当索引原本有该键，目标页页尾合法且逻辑键一致、页尾 `last_ai_revision_id` 大于旧索引值、页面正文与本轮候选的 `content_signature` 相同、且页尾 `source_revisions` 与本轮候选一致时，才以当前页面的已验证页尾和回读 revision 修复索引，不重写页面。此检查先于常规的“页尾与索引不一致即停止”校验；不满足恢复条件时仍报告 `needs_review`，不自动收编。首次发布时若建页成功却没建成索引，也不走该恢复路径。恢复只使用远端索引、远端当前页和本轮候选，不用本地状态文件。索引本身损坏或锁未取得时，不开始目标页写入。

## 5. 报告、状态与旧入口退役

Codex 的真实同步报告保持现有顶层字段 `status`、`candidates`、`published`、`preserved`、`failed`、`run_id`、`source`、`errors`，增加 `retried` 和 `overwritten_human_edits`，删除 `queued`；不新增旧 `conflicts`、`third_party_edits`。`overwritten_human_edits` 为逻辑键升序去重列表，只有在以下条件**全部成立后**才加入：写前当前页 revision 大于索引的 `last_seen_revision_id`；本轮确实执行条件覆盖；写后回读确认页面更新成功。单纯 `preserve`、未发布的 `needs_review` 或失败重试不入列。字段名沿用跨侧约定；revision 前进只说明上次同步后有外部写入，不能单凭它证明操作者身份。

每轮即使有页面失败，也生成可读报告并以 `status=failed`、非零命令退出；已成功覆盖的披露不能因后续索引失败而丢失。新增 `pages` 列表，每项至少含 `key`、`action`、`reason`、`before_revision`、`after_revision`、`page_overwritten`、`index_committed`，使实际写页与索引提交可区分；两个结果字段允许 `true`、`false`、`null`（回读失败而无法确定），未知 revision 也用 `null`。`published` 只统计页与索引都成功的条目；页已覆盖但索引失败计入 `failed`，该页的 `page_overwritten=true`、`index_committed=false`。若写前观察到 revision 前进，且写后回读确认页面确已覆盖，即使随后索引失败，仍须列入 `overwritten_human_edits`；前述“不发布的 `needs_review` 不入列”仅指页面未被覆盖的情形。`retried` 统计 revision 冲突后的实际重试；中途停止导致的未处理候选在 `errors` 中披露，不冒充成功。同步日志和技能回传去掉“入队”，改为发布/保留/失败/重试及覆盖披露。只读的 `verify`/dry-run 报告不产出 `overwritten_human_edits`，若含旧 `queued` 或冲突字段则一并移除。

`needs_review` 保持远端索引中的合法状态，但含义改为“本轮未能安全发布、需要检查或下轮重试”，与队列无关；下一轮不能仅因为该状态就永久短路，安全发布或保留后恢复为 `published`。失败时若已有合法索引条目且可安全提交状态变化，就将该条目标成 `needs_review`；索引不可写或状态提交失败时，至少在本轮报告披露，不能伪称远端已标记。`knowledge-loader` 与 wiki-lint 的提示改成中性的待复核措辞。`archived` 仍是治理状态，不由同步自动恢复，也不改写为 `needs_review`。

`Publisher` 的新初始化和定位不再要求冲突页；已有冲突页可存在且保持原样。删除代码中的队列解析/追加、`store_cli` 的冲突命令、`contract_lint` 的冲突 fixture 依赖，以及 `SyncService`/`MergeDecision` 的旧发布入口。删除后仍须保证索引、锁、页尾元数据、重复逻辑键和索引键页型作用域的既有校验有效；不按 WorkBuddy 的检查项数量机械改写本仓。

## 6. 验收与发布门槛

离线测试覆盖：

1. 同一正文经飞书样式往返后只剩排版差异，决策为保留，目标页和 revision 不前进；纯文字或标点变化决策为发布。
2. URL 目标、代码与有意义的字面 `*`/`_` 变化可检出；只改加粗等纯排版不发布。
3. 写后回读使用同一正文比较规则，页尾元数据仍严格；响应 revision 与回读 revision 不同的测试确认 `last_seen` 取回读值；冲突后重试须先重读、不得沿用旧 revision。
4. 来源修订号不变而候选正文变化时发布；人工直接改页后成功覆盖才出现于 `overwritten_human_edits`；保留、partial、warnings 和重试耗尽均不误报。
5. 资源/评论块、元数据漂移、`needs_review` 下轮重试、`archived`、无索引但有目标页，以及页面写成但索引失败均不进入旧队列、不伪称成功；“写页成功、索引失败、下轮从远端安全修复”有单独回归用例，不满足修复条件时仍停止。
6. 旧远端冲突页仍在；新的解析与发布不依赖它；可选 `source_edit_times` 的旧/新页都能读。同一来源集合且正文不变时，候选有效时间值只刷新远端索引、页零写入；候选缺值且来源向量未变时已有时间不丢失，来源集合变化不拼接出假基线，过期候选时间不回退远端索引。
7. `SyncRunner` 是唯一可执行发布路径，旧 `SyncService`/`merge_protocol` 调用已清场；插件技能、兼容协议、CLI 帮助、报告和测试不再承诺人工优先或冲突裁决。

不执行未经用户授权的远端写入验收。真实 KB 的只读核对可确认页面解析和比较结果，但离线测试与只读核对通过也不解除前三批完成前的跨侧运行纪律。

## 7. 后续批次接口

- 第二批：`docs/superpowers/specs/2026-09-29-codex-kb-ai-source-state-and-admission-design.md`。把 `source_edit_times` 从源快照和候选接入页尾/索引，改用远端索引作为来源增量基线，并交付 `AI_KB_SOURCE_ADMISSION_V1` 的默认纳入与子树排除 P0。
- 第三批：`docs/superpowers/specs/2026-09-29-codex-kb-ai-ingestion-audit-and-correctness-design.md`。处理有真实运行路径的审计、计数、corrections、N2/N7、A1/N4/C3/N6 与 CLI/诊断一致性问题。
- 第四批：`docs/superpowers/specs/2026-09-29-codex-kb-ai-consistency-and-release-closure-design.md`。统一技能、协议、模板、帮助文本与测试，执行只读远端验收和跨侧发布门槛。
- 本批只冻结跨批字段：`source_edit_times` 为可选正整数毫秒映射；索引允许缺失或 `null`，页尾允许缺失；正文权威、唯一发布路径、报告字段和 `needs_review` 语义不得被后续批次改变。
- 第二、三批完成且跨侧只读验收通过前，Codex 与 WorkBuddy 仍不得并发操作同一 KB-AI。

## 8. 实施记录

- 实施提交：`91e5937 feat: make KB-AI content authoritative`。
- 离线验收：
  - `feishu-knowledge-store/scripts`：199 项测试通过。
  - `wiki-ingest/scripts`：76 项测试通过。
  - `knowledge-loader/scripts`：38 项测试通过。
- 静态扫描确认旧 `merge_protocol`、`sync_knowledge`、`target_state`、冲突命令和 `queued` 仅存在于验证其已退役或已移除的测试断言中，不存在运行时发布入口。
- 尚未执行真实飞书写入或跨侧验收；本记录不解除第 1 节和验收门槛中的并发操作禁令。