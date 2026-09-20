---
name: illustration-export
description: 在用户明确导出请求后做本地 HTML 预览导出与资产登记；不调用模型，不生成图片。
---

# Illustration Export

## Boundary

- 只在用户明确要求导出，或确认门批准后调用。
- 先登记资产版本，再执行导出。
- HTML 构建只读取已经确认的 payload 和本地文件存在性，不读取图片像素。
- `embed_images.py` 只做本地字节转码和 data URL 内嵌；不调用模型，不联网。
- 缺失图片必须报错，不得静默省略。
- `--limit-mb` 与 `--quality` 必须显式提供。
- 最终 HTML 超限时保持原文件不变并返回非零。

## Commands

由入口工作流以 Python API 调用：

```python
from build_html import build_html
result = build_html(payload, output_dir)
```

本地内嵌工具：

```powershell
python embed_images.py <html_path> --img-dir <dir> --limit-mb <n> --quality <q>
```
