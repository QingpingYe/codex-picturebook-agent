---
name: image-generate
description: 在用户明确确认后调用图片生成 API；必须显式提供输出目录、尺寸和画质。
---

# Image Generate

## Boundary

- 只有用户明确确认后才能调用本技能。
- 必须显式提供输出目录、尺寸和画质；不得默认取值。
- API key 只能来自 `--api-key` 或 `PICTUREBOOK_SFACAI_KEY` 环境变量。
- 不得打印、记录或提交 API key。
- 本技能只负责生成图片，不负责提示词构建或导出。

## CLI

```powershell
node generate.js --prompt <text> --size <pixels> --quality <level> --output <path> --json
```

可选参数：

- `--refs <path>...`

## Output

成功时输出 JSON：

```json
{
  "output_path": "...",
  "requested_size": "...",
  "actual_size": "...",
  "status": "generated"
}
```

