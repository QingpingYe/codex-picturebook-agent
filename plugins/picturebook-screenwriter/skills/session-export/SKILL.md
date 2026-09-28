---
name: session-export
description: 导出当前会话的审计摘要与已收集内容；输出目录必须由用户显式指定。
---

# Session Export

## Boundary

- 只在用户明确要求导出会话证据时使用。
- 输出目录必须是用户提供的绝对路径。
- 不写入插件安装目录。
- 不读取、推断或记录 API key、令牌、密码或授权头。
- 只写入调用方已经提供的 JSON 安全数据。

路径对比报告也通过本技能写出：`jev-decision-runtime/scripts/compare_cli.py` 复用同一个绝对路径与插件外两个守卫。除报告本身外，它不写任何其它内容。

## Workflow

1. 请用户提供绝对输出目录。
2. 收集本次会话中已经存在的对话事件、产物路径和图片路径。
3. 调用 `scripts/export_session.py`。
4. 用中文返回导出目录和消息、文件、图片数量。
