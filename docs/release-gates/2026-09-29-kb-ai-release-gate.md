# KB-AI 发布门

> 状态：技术门通过；真实 publish 与跨侧并发仍待 explicit user authorization。

## 离线条件

- [x] 第一批、第二批、第三批实现和测试完成。
- [x] Feishu store、wiki-ingest、knowledge-loader 和契约测试全部通过。
- [x] 静态旧口径扫描没有未解释的运行时引用。

## 远端只读条件

- [x] read-only acceptance 记录完成并通过。
- [x] Codex 与 WorkBuddy 对控制页、来源时间、准入和报告字段的静态证据一致。
- [x] 准入缺失、准入损坏、排除子树和来源时间缺失均按 fail-closed 验证；远端未触发的分支由离线测试覆盖。

## 发布条件

- [ ] explicit user authorization 已记录到本文件。
- [ ] publish run owner、预期 revision、停止条件和回滚/修复路径已记录。
- [ ] 只有全部条件满足，才允许解除跨侧并发禁令。

## 离线与远端证据

- [x] Feishu store：222 项通过。
- [x] wiki-ingest：92 项通过。
- [x] knowledge-loader：38 项通过。
- [x] KB-AI 契约与文档一致性：25 项通过。
- [x] picturebook-screenwriter 插件级测试：188 项通过，1 项跳过。
- [x] 远端只读：index revision 24；lock revision 89 空闲；admission revision 5；12/12 页面通过。
- [x] `source_baseline.py` 无 update/create/lock 写调用。
- [ ] explicit user authorization。
