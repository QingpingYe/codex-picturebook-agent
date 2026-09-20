# Self-Contained HTML Template

HTML 必须使用内联 CSS 和内联 JavaScript；不得引用外部 CSS、字体、脚本或图片。

结构按序为：

1. `header`：项目标题、目标年龄段和集数。
2. 可选 `overview`：仅当 `logline` 或 `synopsis` 非空。
3. 可选 `characters`、`locations`、`themes`：仅在清洗后的数组非空。
4. `storyboard`：逐页英文文本、中文译文和插画意图。
5. 可选 `emotion_arc`：至少两页提供合法 `emotion_valence`。

图片池使用 `var IMGS = {...};`。构建阶段 value 是 basename 占位；`embed_images.py` 只将该占位替换为本地源图的 data URL。超限时不得静默丢弃图片。
