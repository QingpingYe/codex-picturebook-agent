---
name: image-prompt-architect
description: 结构化构建绘本生图提示词；只输出 JSON，不生成图片、不编辑图片。
---

# 绘本生图提示词架构师

核心目标：**稳定性、可控性、可复制性**。

> **适用范围**：本 skill 面向**自然语言类图像模型**（如 gpt-image-2 等 OpenAI 兼容图像 API）。这类模型没有独立参数、没有独立的负面提示词字段，完全靠一段提示词文本控制生成。所有规则围绕"如何把控制信息写进这段文本"展开，不涉及重绘强度、ControlNet、`--ar` 等参数。

> **附带文件**：
> - `references/risk-catalog.md` —— AI 生图常见风险与规避方案目录（9 大类 20+ 场景 + 反馈→风险反查表）
> - `references/style-anchor-library.md` —— 多风格通用锚点库（`context.art_style` 命中命名风格时完整展开，禁止压缩）

---

## 输入格式

由入口工作流传入。调用本技能前，必须先运行 `staging-planner`，并把其输出的 `pages[].staging` 原样传入：

```json
{
  "pipeline_mode": true,
  "pages": [
    {
      "page": 1,
      "episode": 1,
      "description": "全景，平视。后景是盛夏丛林……",
      "characters": [
        {"name": "露露", "visual": "渡渡鸟，圆润身体，头顶三根羽毛，暖棕色"},
        {"name": "劳拉", "visual": "人类小女孩，棕色短发，粉色连衣裙"}
      ],
      "scene": "盛夏丛林小径，烈日当空",
      "reference_images": ["{workspace}/outputs/demo/characters/lulu.png", "{workspace}/outputs/demo/scenes/jungle.png"],
      "reference_analysis": "### lulu.png（用途：人设图）\n- 造型特征：头身比约 1:1.6，喙短而钝……\n- 配色：主色暖棕 + 米白腹羽，点缀橘红喙……\n- 笔触与质感：均匀细黑线，无明显粗细变化……\n- 构图与视角：正面全身，视平线与角色胸口齐平……\n- 与本页插图描述的冲突点：无",
      "staging": {
        "camera": {"shot": "全景", "viewpoint": "平视·正面",
                   "world": {"camera_id": "near_bank_shore", "facing": "N"}},
        "characters": [
          {"name": "角色A", "position": "画面前景左侧", "facing": "面朝画面右侧",
           "world": {"zone": "near_bank", "facing": "N"}}
        ],
        "landmarks": [
          {"name": "河流", "layout": "从左到右横向横贯画面",
           "frame_orientation": {"bearing": "horizontal", "from": "left", "to": "right", "in_frame": true},
           "world": {"type": "line", "orientation": {"from": "W", "to": "E"}},
           "char_relation": "角色A 在河流以南（近岸）"}
        ],
        "frame_laws": ["河流在画面中从左到右横向横贯画面中部"],
        "scene_ref_candidates": ["对岸机位场景图", "近岸机位场景图"],
        "cross_page_delta": "相对上一页：无变化（首页为 null）",
        "scene_map_snapshot": "{断点续跑用，正常流程为 null}"
      }
    }
  ],
  "context": {
    "project": "demo",
    "art_style": "明亮温暖儿童绘本，圆润手绘线条，柔和光影",
    "forbidden": ["text", "letters", "scary elements", "dark colors", "crying/fearful expressions"],
    "illustration_spec": "{插画规范条目正文，由调用方从 injected_knowledge 取得；缺失时置 null}"
  }
}
```

> `staging` 可选：由调用方的站位构图规划器（staging-planner）产出后原样传入；未执行规划时整个字段省略。**本技能不自行构造 staging**——字段缺失时回退为从 `description` 提取方位词（纯文字路径，行为与现状完全一致）。
>
> `staging` 的新增字段（`world` / `frame_orientation` / `frame_laws` / `scene_ref_candidates` / `scene_map_snapshot`）全部**可选**，向后兼容 v1 构图表；frame_laws 由 staging-planner 从世界系账本投影产出，本技能只消费不推导。
>
> `context.art_style` 取值约定：绘本插图模式由调用方传入「画风来源确认」的值（用户画风文字或画风参考图视觉摘要归纳），**无默认值**；资产前置素材图模式由调用方**固定**传入「黑白线条手绘素描」（命中风格锚点库锚点「黑色线条手绘素描」）。本技能不自行决定画风来源。

---

## 铁律

- **只输出结构化 JSON；绝对禁止生成图片，也不编辑任何图片**
- **绝不调用任何图片生成或图片编辑 API**；本技能只负责构建提示词文本
- **只返回一个 JSON 对象**，不用 Markdown 代码块
- **必须在 prompt 中使用参考图的完整文件名**（如 `lulu_v2.png`、`jungle_scene_v1.png`），确保下游图像模型能正确识别和引用参考图。多参考图时逐一列出各文件名及用途，不得用抽象代号
- **`pages[].staging.frame_laws` 必须从 `staging-planner` 输出逐字复制**，不得改写、压缩、推导或省略
- **不做冗长教学**，不做阶段声明
- **步骤 3–5 的分析全部内部完成，不输出**
- **仅输出中文 prompt**，不生成英文版
- **返回结构化 JSON**（`prompt` 数组 + `prompt_text` + `risks_found` + `assumptions`），不用 Markdown 代码块
- **不等待用户反馈**，直接返回结果给入口工作流
- **风格行锚点展开不可压缩**：`context.art_style` 命中 `references/style-anchor-library.md` 命名风格时，风格行必须完整展开该锚点原文，禁止精简/压缩；修改/重试模式下强制复用锚点原文（详见该文件「使用规则」）
- `references/risk-catalog.md` 与 `references/style-anchor-library.md` 是本技能仅有的风格风险参考资料；不得读取 WorkBuddy 本地库或项目外的样式文件

---

## 工作流程

### 步骤 1：接收输入

从 `pages[].description` 中取画面描述。若画面细节不足（主体不清晰，或环境/构图/光影/风格中少于两项），利用已有信息自动补全：

- 角色视觉 → 优先取 `pages[].characters[].visual`；若 characters 为空但 description 中提到角色名 + 外观特征，从 description 文本中提取；二者皆无则根据角色名使用通用绘本角色描述，记录假设
- 场景 → 取 `pages[].scene`；空则从 description 中提取场景关键词
- 风格 → 取 `context.art_style`；空则回退到通用儿童绘本风格
- 记录所有自动补全项到 `assumptions` 数组
- staging 字段缺失时不补全、不推断——记入 assumptions："未提供 staging，方位/机位意图从 description 文本提取"

**修改模式**：若 `context` 中含 `modification_request` 字段，则本次为已有页面的修改。将 `modification_request` 内容作为最高优先级的额外意图，覆盖原有相关意图（如改颜色 → 替换原颜色意图），但仍走完整步骤 2→3→4→5，确保修改后风险重新评估。

不打断，不输出方案选择。

### 步骤 2：需求与意图解析（内部）

将每页的画面描述拆解为独立的意图条目：

- 意图 1：[画面要求] → 对应画面元素：[...]
- 意图 2：[画面要求] → 对应画面元素：[...]
- 任务类型：文生图 / 图生图
- 图生图子模式判定：
  - **改图模式**：信号词「修改/改成/把…换成/去掉/加上/调整」→ 保留大部分内容，只描述改动 + Keep 锚点
  - **参考提取模式**：信号词「参考/提取/用这个角色/照着这个风格/借鉴」→ 只搬被点名元素，其余全新构建
  - 绘本场景默认用**参考提取模式**（有 reference_images 且非空时）
- 隐含需求推断
- 意图间冲突排查

**拆解原则**：主体、动作、环境、风格、情绪、镜头、光影、文字——每个都是独立条目。

**`staging` 字段的优先级**：`pages[].staging` 存在时，景别/机位/角色方位/地标走向的意图一律以 staging 为准，优先于 `description` 中的方位词；`description` 中与 staging 冲突的方位词忽略，并记入 assumptions（"description 方位词与 staging 冲突，已按 staging 落位"）。`staging.cross_page_delta` 注入为跨页意图条目，参与意图间冲突排查。`staging.frame_laws` 是帧内绝对方位的权威来源，与 description 方位词冲突时以 frame_laws 为准并记入 assumptions。修改模式下 `modification_request` 仍为最高优先级（可覆盖 staging 单页字段）。

**`reference_analysis` 的优先级**：该字段存在时，它是调用方对参考图的**实际观察结果**，参考图的造型/配色/笔触/构图一律以它为准，优先于 `characters[].visual` 的文字设定（后者是设定稿，可能与图上实际画出来的有出入）；字段中记录的「与本页插图描述的冲突点」并入意图间冲突排查。字段缺省时按纯文字路径处理，依据 `characters[].visual` 与 `scene` 撰写即可——**本技能自身不读取任何图片**，缺省不是让你去补看图。

#### 绘本上下文注入

拆分完成后，注入以下意图条目（来自 `context`，不覆盖已有明确意图）：

| 注入项 | 来源 | 意图条目 | 缺失处理 |
|---|---|---|---|
| 角色视觉约束 | `pages[].characters[].visual` | "角色 {name}：{visual}，保持造型一致" | characters 为空时，从 description 文本中提取角色外观描述；两者皆无则使用通用描述并记录假设 |
| 场景氛围 | `pages[].scene` | "场景设定：{scene}" | 场景为空时从 description 文本提取场景关键词 |
| 艺术风格 | `context.art_style` | "艺术风格：{art_style}"（命中锚点库时展开为该锚点的「完整展开段落」） | 为空时回退到通用儿童绘本风格 |
| 禁止项 | `context.forbidden` | "禁止：{forbidden 列表}" | 为空时使用 `context.illustration_spec` 的「禁止底线」；两者皆空时使用通用底线（禁止文字/恐怖/阴暗/负面表情）并记录假设 |
| 修改请求 | `context.modification_request` | 以最高优先级注入为额外意图 | 无则不注入 |

**风格锚点展开规则**：`context.art_style` 命中 `references/style-anchor-library.md` 中命名风格时，将「艺术风格」意图条目的值替换为该锚点的「完整展开段落」原文，**禁止精简/压缩风格行**；修改/重试模式下强制复用锚点原文。存在参考图时，风格行之后追加「颜色与质感由风格锚点主导，参考图仅提取角色造型特征，不搬运参考图的色彩与质感」。完整规则见该文件「使用规则」。

注入后执行冲突排查。项目级约束与原始意图冲突时以项目约束为准，标注 `[项目约束覆盖]`。

### 步骤 3：防翻车预判（内部）

查询 `references/risk-catalog.md`，逐条意图匹配触发条件，只记录真实命中的风险。

**操作流程**：
1. 读取风险目录文件
2. 每一条意图 × 风险目录逐条比对触发条件，命中则记下风险 + 规避方案
3. 没命中的整类跳过，没有任何风险则本步骤为空
4. 为每个命中风险确定具体落实点：哪一行写、写什么，不停在空话

**内部格式**：
- 命中风险：[风险名（编号）] ← 来自意图 [X] → 规避落实：[具体写法]
- （无命中则"无显著风险"）
- 跨页意图命中时参考风险目录「构图与空间」类目下「跨画面空间漂移」「物体朝向与镜头朝向矛盾」两条

### 步骤 4：动态结构设计（内部）

基于步骤 2–3 的分析，设计提示词结构：

- 选取必要模块（只用必要的，冗余堆叠稀释信号）：
  Subject / Action / Environment / Spatial Layout / Lighting / Camera / Style / Constraints
- 确定行序：主体 → 空间 → 光影 → 镜头 → 风格 → 负面约束
- 有 `staging` 时：Spatial Layout 行按 `staging.characters` 逐角色落位（"前景左侧：{角色名}，面朝画面右侧"），`staging.landmarks` 按 `layout` + `char_relation` 落位；Camera 行按 `staging.camera` 写"`{shot}`构图，`{viewpoint}`视角"。无 staging 时按现状从 description 提取方位词
- 有 `staging.frame_laws` 时：Spatial Layout 行逐条**原样落行**帧内绝对方位；**不再**写「垂直于画面」类相机相对词
- `landmarks[].frame_orientation` 与 `layout` 并存时以 frame_orientation 为准（layout 为 v1 兼容字段）
- 插描未提地标的页 frame_laws 照常注入（新增行为）
- `in_frame: false` 的地标不落行任何画面内方位（`layout` 与 frame_laws 均不写，防负面点名召唤，见本文件「负面约束规则」的双刃剑条目）
- frame_laws 每页最多取 3 条（按叙事重要性）
- 标记视觉锚点
- 参考提取模式：明确提取清单 + 全新构建清单，分开陈述，加防搬运约束

### 步骤 5：输出结构化提示词

对 `pages` 数组中的每一页，独立跑步骤 1→2→3→4→5，汇总为 JSON：

```json
{
  "pages": [
    {
      "page": 1,
      "episode": 1,
      "prompt": [
        "全景横构图儿童绘本插画，平视视角",
        "后景：盛夏丛林，茂密的热带植物，阳光透过树叶洒下斑驳光影",
        "中景：一条蜿蜒的丛林小径",
        "前景：露露，一只圆润可爱的渡渡鸟，暖棕色羽毛，头顶三根放松翘起的羽毛，跑在画面前方朝右，身体前倾，翅膀微微张开"
      ],
      "prompt_text": "全景横构图儿童绘本插画，平视视角。后景：盛夏丛林，茂密的热带植物，阳光透过树叶洒下斑驳光影。中景：一条蜿蜒的丛林小径。前景：露露，一只圆润可爱的渡渡鸟，暖棕色羽毛，头顶三根放松翘起的羽毛，跑在画面前方朝右，身体前倾，翅膀微微张开。",
      "risks_found": ["6.2 克隆脸 — 5 个角色需差异化面部"],
      "assumptions": ["使用通用绘本风格基线（无项目级风格参考图）"],
      "applied_context": {
        "art_style": "明亮温暖儿童绘本，圆润手绘线条",
        "forbidden_injected": true,
        "characters_injected": ["露露", "劳拉", "Miko", "Slow", "Popo"]
      }
    }
  ],
  "pipeline_meta": {
    "total_pages": 1,
    "mode": "pipeline"
  }
}
```

**字段说明**：

| 字段 | 说明 |
|---|---|
| `prompt` | 分行结构化 prompt 数组，每行一个语义模块，供审查和日志 |
| `prompt_text` | 拼接好的单行完整文本，**可直接传入图像生成 API** |
| `risks_found` | 步骤 3 命中的风险清单，供确认清单展示 |
| `assumptions` | 步骤 1 自动补全的假设，供确认清单标注 |
| `applied_context` | 实际注入的上下文摘要，供审计 |

**prompt_text 拼接规则**：将 `prompt` 数组用 `"。"` 拼接为单行文本，末尾不加句号。

---

## 提示词排版规则（CRITICAL）

每个语义模块**单独一行**。不能把所有内容挤在一整行里；碎片化或长串堆叠都会降低控制精度。

✅ 正确：
```
一个男人站在房间中央，
温暖的阳光从左边的窗户照进来，
平视视角，50mm 镜头，
```

❌ 错误（碎片化）：`一个男人,` / `站着,` / `在房间里,`

❌ 错误（长串堆叠）：`一个男人站在房间中央，温暖的阳光从左边的窗户照进来，平视视角，50mm 镜头`

### 空间与构图规则

用明确空间锚点，避免模糊措辞（`旁边` 会让模型随机发挥）：
- **方位**：左侧 / 右侧 / 前方 / 后方
- **层级**：前景 / 中景 / 后景
- **距离**：具体数值或参照（"两米远"/"在画面边缘"）

### 光影规则

每个场景明确三件事：光源方向 + 光源色温/颜色 + 时间或环境。

### 负面约束规则

负面约束用自然语言嵌入（`不要`/`禁止`/`无`），**统一放在提示词的最后一行**。

**关键（双刃剑）**：正向描述中绝不出现任何不想要的词——在正向描述里点名不想要的对象，反而会把它召唤出来。所有"不想要"只在末行集中处理。

---

## 图生图规则

### 参考提取模式（绘本默认）

参考图是素材来源，默认只搬被点名元素，其余全新构建。

开头声明行（使用实际文件名）：
```
仅从参考图 [实际文件名如 lulu_v2.png] 中提取 [具体元素]，
```

必须做到：
- 点名提取对象到具体特征：不写"用这个角色"，写"从参考图中提取角色的面部、发型和服装"
- **有 `reference_analysis` 时，提取对象要点到摘要里实际观察到的特征**（如"提取暖棕主色 + 米白腹羽的配色关系与均匀细黑线笔触"），不停留在"角色造型"这类泛指——摘要的价值就在这里，泛指等于白看
- 场景参考图的提取声明行必须点名「仅提取植被/质感清单」（如河岸土质、蕨类、急流质感），不能只写「提取场景元素」这类泛指
- 多张参考图逐一列出实际文件名及用途：`[lulu_v2.png] 提供角色造型，[jungle_scene_v1.png] 提供场景风格`
- 末行防搬运约束：调用方提供了帧向铁律（staging.frame_laws 或任务上下文中显式注入的帧向铁律文本）时，写「不要复制原图的背景或构图；参考图中的地标走向与机位一律忽略，本页地标走向以帧向铁律为准」；未提供时沿用「不要复制原图的背景或构图」
- 融合一致性：提取元素须与新场景光影/透视/风格统一，必要时写 `重新打光以匹配新场景`

### 输出模板

**文生图**（无 reference_images 时）：
```
[主体行]，
[空间/环境行]，
[光影行]，
[镜头行]，
[风格行]，
禁止 [...]，不要 [...]。
```

**参考提取**（有 reference_images 时）：
```
仅从参考图 [文件名如 lulu_v2.png] 中提取 [具体元素；场景图点名「仅提取植被/质感清单」]，
[新背景/环境行 — 全新构建]，
[新空间/构图行]，
[新光影行，并使提取元素融入]，
[镜头行]，
[风格行]，
[多图时: [文件名2] 提供 ...]，
不要复制原图的背景或构图；参考图中的地标走向与机位一律忽略，本页地标走向以帧向铁律为准（调用方提供帧向铁律时；未提供时末行只写「不要复制原图的背景或构图」）。
```
