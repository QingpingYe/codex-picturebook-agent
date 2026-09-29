# KB-AI 发布门

> 状态：offline complete；远端 read-only acceptance 与真实 publish 仍待授权。

## 离线条件

- [ ] 第一批、第二批、第三批实现和测试完成。
- [ ] Feishu store、wiki-ingest、knowledge-loader 和契约测试全部通过。
- [ ] 静态旧口径扫描没有未解释的运行时引用。

## 远端只读条件

- [ ] read-only acceptance 记录完成并通过。
- [ ] Codex 与 WorkBuddy 对控制页、来源时间、准入和报告字段一致。
- [ ] 准入缺失、准入损坏、排除子树和来源时间缺失均按 fail-closed 验证。

## 发布条件

- [ ] explicit user authorization 已记录到本文件。
- [ ] publish run owner、预期 revision、停止条件和回滚/修复路径已记录。
- [ ] 只有全部条件满足，才允许解除跨侧并发禁令。

## 离线证据

- [x] Feishu store：218 项通过。
- [x] wiki-ingest：92 项通过。
- [x] knowledge-loader：38 项通过。
- [x] KB-AI 契约与文档一致性：25 项通过。
- [x] picturebook-screenwriter 插件级测试：188 项通过，1 项跳过。
- [x] `source_baseline.py` 无 update/create/lock 写调用。
- [ ] 远端 read-only acceptance。
- [ ] WorkBuddy/Codex 跨侧确认。
- [ ] explicit user authorization。
