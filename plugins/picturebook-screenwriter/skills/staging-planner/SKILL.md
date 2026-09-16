---
name: staging-planner
description: 绘本构图站位规划器 v2。在 Codex 会话内以流水线模式调用：输入 pages[]（插描原文+英文文本+出场角色+场景）+ context.scene_map（脚本「场景俯视图（空间账本）」空间账本 JSON）+ 可选场景台账文字（空间锚点），一次性为全集所有页面（含插描未提地标的页）产出跨页分镜构图表——每页景别/机位/角色画面方位/地标帧内走向（帧向铁律 frame_laws），帧向由确定性投影脚本 project_scene_map.js 从世界系账本推导。输出含跨页漂移检测 FLAG。通用方法论，项目专属空间锚点运行时注入；不读取任何图片。
applies_to:
  slot: none
  type: utility
---

# 绘本构图站位规划器 v2（场景俯视图跨页空间一致性）

## 目标

把逐页插图描述 + 世界系空间账本（scene_map）转成**跨页自洽的分镜构图表**：给每页定死机位（camera_position）/景别/角色画面方位/地标帧内走向（帧向铁律 frame_laws），并检测跨页空间漂移（角色×地标相对方位无叙事依据的变化）与插描-账本矛盾。与 v1 的关键差异：**帧内方位不再靠 LLM 临场判断，而是由确定性投影脚本从世界系账本推导**——同一机位下地标的帧内走向是确定值，逐页按机位查表复制，插描未提地标的页同样拿到帧向铁律。构图表是生图提示词的站位权威输入，也是生成后站位校验的比对基准。

## 适用范围

仅供入口工作流在会话内调用，不做人工交互对话，不等待用户反馈，直接返回结果。

## 铁律

- **只输出结构化 JSON**，不用 Markdown 代码块
- **本技能自身不读取任何图片**——`reference_analysis` 是调用方传来的文字摘要，直接消费文字，不为看参考图做任何读图操作
- 只输出中文
- **不落盘**——构图表默认随调用结果回传；落盘仅由调用方在用户显式要求导出时执行
- 空间锚点一律运行时由调用方注入，本文件不承载任何项目专属锚点

## scene_map schema（权威定义）

世界系约定：俯视平面，北=纸面上方、东=右；方位仅用八方位 `N/NE/E/SE/S/SW/W/NW`（顺时针 0–7 编码）；账本只存世界系，**禁止出现「画面左/右」**（帧内方位一律由投影推导）；坐标是定性尺度（谁在谁哪边），不做米制。

```json
{
  "schema_version": 1,
  "coordinate": {"north": "up", "bearing_bits": 8},
  "landmarks": [
    {"id": "river", "name": "河流", "type": "line",
     "orientation": {"from": "W", "to": "E"}, "flow": "E",
     "extent": [{"x": -5, "y": 0}, {"x": 5, "y": 0}], "zones": ["near_bank", "far_bank"]},
    {"id": "bridge", "name": "独木桥", "type": "line",
     "orientation": {"from": "S", "to": "N"},
     "extent": [{"x": 0, "y": -1}, {"x": 0, "y": 1}], "zones": ["near_bank", "far_bank"]},
    {"id": "berry_bush", "name": "灌木丛", "type": "area", "zone": "far_bank", "anchor": {"x": -2, "y": 2}}
  ],
  "zones": [
    {"id": "near_bank", "name": "近岸", "define": "河流以南"},
    {"id": "far_bank", "name": "对岸", "define": "河流以北"}
  ],
  "characters": [{"name": "角色A", "position": {"zone": "near_bank", "x": 0, "y": -2}, "facing": "N"}],
  "camera_positions": [
    {"id": "near_bank_shore", "name": "近岸岸边", "position": {"zone": "near_bank", "x": 0, "y": -4}, "facing": "N"}
  ],
  "page_ledger": [
    {"page": 5, "camera": "near_bank_shore",
     "deltas": [{"character": "角色A", "from": "near_bank", "to": "on_bridge", "basis": "Text 移动词原文引用"}]}
  ]
}
```

字段说明：

- `landmarks[].type`：`line`（河/桥/路）/ `area`（灌木丛/空地，用 `anchor` 定位）/ `point`（树/石头，用 `anchor`）；线型加 `orientation {from,to}`（八方位），河流等有流向的加 `flow`（八方位）。
- `zones[].define`：相对地标的空间关系定义（「河流以南」），不硬编码绝对坐标。
- `camera_positions[]`：离散命名机位（id/name/position/facing）。
- `page_ledger[]`：逐页登记本页机位 + 移动账本（谁从哪移到哪 + Text 原文依据）。
- 投影产物是离散「机位 × 地标帧向对照表」：每个机位的地标帧向是确定值，逐页按机位查表复制。

### 附录 → scene_map 映射规则（解析契约，入口工作流执行）

如果逐页脚本带有「场景俯视图（空间账本）」附录，按以下解析契约转换各小节 → JSON：

| 附录小节 | JSON 字段 | 转换规则 |
|---|---|---|
| 地标登记表 | `landmarks[]` | 「几何类型」列取 line/area/point；「世界走向」列填八方位对（如「西→东」）→ `orientation {from:"W",to:"E"}` |
| 区域表 | `zones[]` | 「定义」列直接复制 |
| 机位登记表 | `camera_positions[]` | 「朝向」列的八方位词（朝北/朝南/…）→ `facing` 编码（N/S/E/W/NE/NW/SE/SW） |
| 逐页移动账本表 | `page_ledger[]` | 每行一条：页码 → `page`、机位列 → `camera`、移动记录 → `deltas[]`（「依据」列取 Text 原文引用） |

## 输入格式

由入口工作流或用户传入：

```json
{
  "pages": [
    {
      "page": 1,
      "episode": 1,
      "description": "全景，平视。角色A在独木桥中段，面朝画面深处；桥从前景向纵深延伸……",
      "text": "The bridge wobbled under his feet.",
      "characters": [{"name": "角色A", "visual": "……"}],
      "scene": "深林小桥，午后"
    }
  ],
  "context": {
    "scene_map": "{从脚本「场景俯视图（空间账本）」附录按映射规则转换的 JSON，schema 见本文件「scene_map schema」；缺失时走 v1 兼容路径（见步骤 0）}",
    "space_anchors": "{项目场景台账（{project_root}/scene-ledger.md）中本集相关场景节的文字（地标登记/区域/机位登记三表），作为 scene_map 的第二来源用于校验/补缺；台账不存在或该场景未登记时置 null（本集为首登，不阻断）}",
    "reference_analysis": "{可选：参考图视觉分析文字摘要，仅『构图与视角』小节可作参考；未做视觉分析时整个字段省略}",
    "previous_batch_anchor": "{可选：上一批的 scene_map_snapshot，仅会话中断后的断点续跑用}"
  }
}
```

## 工作流程

### 步骤 0：接收输入

1. 解析 `context.scene_map`，用 `space_anchors` 校验/补缺（锚点与账本矛盾时以账本为准并记入 assumptions）。
2. **scene_map 缺失 → 从插描方位词 + space_anchors 反推初始 scene_map**，assumptions 标注「账本缺失，为推断值」（v1 兼容路径，不阻断）。

### 步骤 1：一次性全集计算（一次调用覆盖全部页面，不逐批增量）

1. **逐页更新状态**：`text` 移动动词（跳/爬/过桥/走到/来到…）+ 插描移动断言 → 更新 `characters` 位置/朝向，登记 `page_ledger`。
2. **逐页机位放置**：主视点 → 匹配/新建 `camera_position`；连续页机位默认延续，变化必须有本页显式依据（场景切换/明确视角切换）→ 否则 FLAG `{type: "camera_basis_missing"}`。
3. **机位帧向预计算**：对每个 `camera_position` 调 `project_scene_map.js` 一次（地标部分与机位绑定、与页无关；此时 `characters` 传空数组，取 `landmarks[].frame_orientation` 与 `frame_laws`），得「机位 × 地标帧向对照表」。
4. **逐页组装 staging**：机位查表复制 frame_laws（插描留白页同样拿到）+ 角色投影（按该页角色状态再调脚本，取 `characters[]` 的 screen_position/facing_relative/in_frame 与 `landmarks[].char_relations`，经下方「脚本输出 → SSOT 字段映射」转换后写入）+ world 字段 + scene_ref_candidates。
5. **与插描原文比对** → flags（见「flags 扩展」）。

**脚本输出 → SSOT 字段映射**（逐页组装时执行——脚本输出形态与 SSOT staging 字段形态不同，不得直接写回）：

- `landmarks[].in_frame`（地标级）→ 迁入 SSOT 的 `frame_orientation.in_frame`（`frame_orientation` 由机位帧向预计算产出，组装时补 `in_frame` 字段；SSOT 地标对象不保留地标级 `in_frame`）。
- `landmarks[].char_relations[]`（数组、纯方位，如「角色A 位于河流以南」）→ SSOT 的 `char_relation`（单字符串）：数组项用「；」并句；每项把「位于」改写为「在」并按 scene_map 中角色的 `position.zone` 补 zone 标注——「角色A 位于河流以南」→「角色A 在河流以南（近岸）」；多项并句如「角色A 在河流以南（近岸）；角色A 在独木桥以东（近岸）」。

### 步骤 2：输出

`pages[].staging`（全页，含插描未提地标的页）+ `flags[]` + `continuity_plan`。`scene_map_snapshot` 仅用于会话中断后的断点续跑，正常流程不依赖跨批状态传递。

## 确定性投影（project_scene_map.js）

脚本位置 `skills/staging-planner/scripts/project_scene_map.js`（Node.js，零依赖）。调用方式：stdin 读一个 JSON 对象（`{landmarks, characters, camera}`），stdout 写投影结果 JSON，**不写任何文件**；失败时非零退出并向 stderr 输出错误信息。

**两种调用形态**：

- **机位帧向预计算**（步骤 1.3）：`characters` 传空数组，取 `landmarks[].frame_orientation` + `frame_laws` → 机位 × 地标帧向对照表。
- **逐页角色投影**（步骤 1.4）：传该页角色状态，取 `characters[]` 的 screen_position/facing_relative/in_frame + `landmarks[].char_relations`。

### 地标帧内走向（与脚本实现一致）

设相机朝向编码 `C`、地标延伸方向 `L`（orientation.to 的编码）、`rel = (L - C + 8) % 8`：

| rel | 帧内走向（bearing/from/to） | frame_law 文本模板 |
|---|---|---|
| 0 | vertical / bottom / top（前景→背景延伸） | `{name}从画面前景向背景纵向延伸` |
| 4 | vertical / top / bottom（背景→前景延伸） | `{name}从画面背景向前景纵向延伸（迎向镜头）` |
| 2 | horizontal / left / right | `{name}在画面中从左到右横向横贯画面` |
| 6 | horizontal / right / left | `{name}在画面中从右到左横向横贯画面` |
| 1 | diagonal / bottom-left / top-right | `{name}在画面中沿对角线延伸（从左下斜向右上）` |
| 3 | diagonal / top-left / bottom-right | `{name}在画面中沿对角线延伸（从左上斜向右下）` |
| 5 | diagonal / top-right / bottom-left | `{name}在画面中沿对角线延伸（从右上斜向左下）` |
| 7 | diagonal / bottom-right / top-left | `{name}在画面中沿对角线延伸（从右下斜向左上）` |

- **视锥 in_frame 检查**：对象相对相机的方位角（bearing）落在 `{C-2 … C+2} mod 8` 扇区 → 入画；扇区外 → `in_frame: false`，**不产出该地标的 frame_law**（不写正向铁律，防负面点名召唤）。line 型地标取两个端点 + 中点，任一落在扇区即入画；area/point 取 anchor。
- bearing 计算：`angleDeg = atan2(dx, dy) * 180 / PI`（0°=北，顺时针为正），`bearing = ((Math.round(angleDeg / 45)) + 8) % 8`。
- `frame_laws` 数组：按 landmarks 输入顺序收集入画地标的铁律文本（脚本不裁剪——精简 ≤2-3 条是消费方规则，见「frame_laws 精简原则」）。

### 角色画面方位

`rel_pos = (bearing(角色位置 − 相机位置) − C + 8) % 8`：

| rel_pos | 画面方位 |
|---|---|
| 0 | 画面中上部（后景中央方向） |
| 1 / 2 | 画面右侧 |
| 3 | 画面下侧 |
| 4 | 相机身后 → **不入画**（in_frame=false，不产出 screen_position） |
| 5 / 6 | 画面左侧 |
| 7 | 画面左上方（上侧偏左） |

距离档位：`|d| < 1.5` 前景、`1.5 ≤ |d| < 4` 中景、`≥ 4` 后景（定性阈值，脚本头注释说明可调）。`screen_position` = 方位词 + 档位（如「画面右侧·中景」）。

### 角色相对镜头朝向

`rel_f = (facing − C + 8) % 8`：

| rel_f | 朝向 |
|---|---|
| 0 | 背对镜头 |
| 1 | 面朝画面右上方（3/4 背对镜头） |
| 2 | 面朝画面右侧 |
| 3 | 面朝画面右下方（3/4 面向镜头） |
| 4 | 面朝镜头 |
| 5 | 面朝画面左下方（3/4 面向镜头） |
| 6 | 面朝画面左侧 |
| 7 | 面朝画面左上方（3/4 背对镜头） |

### 角色 × 地标相对方位（char_relations）

入画角色 × 入画地标两两产出 `{角色名} 位于{地标名}{方位中文}`：方位取**角色相对地标的 bearing**（角色在地标的哪一侧），方位中文为 以北/以东北/以东/以东南/以南/以西南/以西/以西北——如角色A 在河流以南 → 「角色A 位于河流以南」。line 型地标以中点计，area/point 以 anchor 计。

### 降级协议

脚本缺失时（由调用方检测），按本文件规则表手工推演，并在 assumptions 标注「投影由 LLM 推演，未经确定性校验」。

## frame_laws 精简原则

- 每页帧向铁律 ≤2-3 条（仅入画的核心地标，按叙事重要性取舍）。
- 帧向文本用绝对画面语言，禁止「垂直于画面」类相对词。
- `in_frame: false` 的地标不写正向铁律（防负面点名召唤，避免“不要出现”式提示反而强化目标）。

## 跨页漂移检测（保留 v1 四规则）

判定语汇统一升级为**以投影结果为基准**：

- **规则 1（角色×地标方位连续性）**：同角色+同地标相对方位在连续页间变化时，必须在前后页 `text`/`description` 中找到移动依据（以 `page_ledger` 登记为准）；找不到 → FLAG `{type: "cross_page_drift", page_pair: "Pn-Pm", issue: "……", suggestion: "……"}`。
- **规则 2（地标走向稳定性）**：同 scene 连续页间地标帧内走向不得突变（桥不忽纵忽横）；突变 → FLAG `{type: "landmark_flip", ...}`（description 明示场景切换或镜头旋转者豁免）。走向以「机位 × 地标帧向对照表」为准——**机位变化导致的走向变化是投影结果，不判 flip**。
- **规则 3（朝向几何一致性）**：角色身体朝向 + 运动方向 + 地标延伸方向须几何可摆位；矛盾 → FLAG `{type: "facing_conflict", ...}`，改写建议优先改地标走向、保角色朝向。
- **规则 4（批次连续性）**：`previous_batch_anchor` 非空时，本批首页与该锚点页执行规则 1；输出 `continuity_plan.next_batch_anchor` = 本批末页的 scene_map_snapshot（账本快照），供断点续跑。

## flags 扩展（在 v1 四规则基础上新增三类）

| FLAG 类型 | 触发条件 | 示例 |
|---|---|---|
| `camera_basis_missing` | 连续页机位（position 或 facing）变化但本页插描/叙事无显式依据 | `{type: "camera_basis_missing", page: "Pn", issue: "机位从近岸岸边切到对岸，Pn 插描无视角切换依据", suggestion: "本页补视角切换依据，或延续上一页机位"}` |
| `description_map_conflict` | 插描空间断言与投影结果矛盾（插描写「桥横向」但投影为纵向） | `{type: "description_map_conflict", page: "Pn", issue: "插描写「桥横向」，投影结果为纵向", suggestion: "以投影为准改插描，或修正账本中桥的走向"}` |
| `landmark_not_in_ledger` | 插描出现账本未登记的地标 | `{type: "landmark_not_in_ledger", page: "Pn", issue: "插描出现「磨坊」，账本未登记", suggestion: "要求编剧补登记"}` |

`flags` 不自行裁断——随构图表回传，由调用方并入确认清单交用户把关。

## 输出构图表

每页 `staging` 字段的 schema 以本文件的输出构图表为准，可被后续图片提示词流程复用。输出信封：

```json
{
  "pages": [
    {
      "page": 1,
      "staging": {
        "camera": {"shot": "全景", "viewpoint": "平视·正面", "world": {"camera_id": "near_bank_shore", "facing": "N"}},
        "characters": [{"name": "角色A", "position": "画面中上部·中景", "facing": "背对镜头", "world": {"zone": "near_bank", "facing": "N"}}],
        "landmarks": [{"name": "河流", "layout": "从左到右横向横贯画面", "frame_orientation": {"bearing": "horizontal", "from": "left", "to": "right", "in_frame": true}, "world": {"type": "line", "orientation": {"from": "W", "to": "E"}}, "char_relation": "角色A 在河流以南（近岸）"}],
        "frame_laws": ["河流在画面中从左到右横向横贯画面"],
        "scene_ref_candidates": ["对岸机位场景图", "近岸机位场景图"],
        "cross_page_delta": "相对上一页：无变化（首页为 null）",
        "scene_map_snapshot": "{断点续跑用，正常流程为 null}"
      }
    }
  ],
  "flags": [
    {"type": "cross_page_drift", "page_pair": "P3-P4", "issue": "角色A相对独木桥从右侧变左侧，P3/P4 文本无移动依据", "suggestion": "P4 补移动依据（如 text 加『跳到了对岸』），或改回桥右侧"}
  ],
  "continuity_plan": {"batch": 1, "next_batch_anchor": "{本批末页 scene_map_snapshot}"}
}
```

## 降级

- `scene_map` 缺失不阻断——从插描+space_anchors 反推（v1 兼容路径），标注 assumptions「账本缺失，为推断值」
- 投影脚本缺失不阻断——按规则表手工推演，标注 assumptions「投影由 LLM 推演，未经确定性校验」
- `space_anchors` 缺失不阻断——仅用 scene_map 与插描提取，标注 assumptions
- `flags` 不自行裁断——随构图表回传，由调用方并入确认清单交用户把关
- 插描本身缺站位信息时按 description 现有方位词规划并在 assumptions 标注"插描缺站位要素，构图表为推断值"

## References

- `scripts/project_scene_map.js` — 确定性投影脚本
- `scripts/run_regression.js` — 回归测试
