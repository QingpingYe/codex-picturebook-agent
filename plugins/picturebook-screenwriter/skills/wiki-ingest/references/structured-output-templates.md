# 结构化输出模板参考

本文件定义 v9 wiki-ingest 生成的所有结构化文件的 frontmatter 模板、必须章节和格式规范。所有模板基于 scriptwriting-agent 的 wiki/ 结构设计。

---

## Frontmatter 通用字段

所有文件共享以下 frontmatter 字段集：

```yaml
title: "{页面标题}"              # 必填
series_id: "海外绘本"            # 必填
project_id: "{项目标识}"         # 项目级必填；系列通用页型写 common；index/log 为空
page_type: "{页面类型}"          # 必填：枚举值见各模板
source_feishu_url: "{飞书源URL}" # 必填，多个源用逗号分隔
source_feishu_title: "{飞书源标题}"  # 必填
revision_id: "{revision_id}"     # 必填
extracted_at: "ISO时间"          # 必填
source_node_tokens:                      # 必填：来源 Wiki 节点 token 列表
  - "node-token-a"
source_revision_parts:                   # 必填：与来源节点 token 一一对应的修订号
  - "42"
# 可选：source / source_web / web_digest / obsolete
```

`source_node_tokens` 与 `source_revision_parts` 必须长度一致、顺序一致，且都不得为空。它们是同步阶段的来源版本向量；显示标题可以变化，但 `series_id/project_id/page_type` 构成的逻辑键保持稳定。

## Manifest 契约

校验通过后，`wiki_staging/_manifest.json` 保留原有的 `root/series` 层级结构，并新增确定性的扁平 `entries` 列表。每条记录至少包含：

```json
{
  "path": "worldview.md",
  "key": "海外绘本/小老鼠迈尔斯/worldview",
  "page_type": "worldview",
  "series_id": "海外绘本",
  "project_id": "小老鼠迈尔斯",
  "source_revisions": {
    "node-token-a": "42"
  }
}
```

---

## 一、系列通用（common/）

### 1.1 ip-overview.md — IP 总览

```yaml
page_type: "ip-overview"
project_id: "common"
```

**必须章节：**
- 概述
- IP 矩阵（表格：IP 名称 / 主角 / 世界观位置 / 核心主题 / 出版规格 / 状态）
- 品牌定位
- 各IP 详细说明
- 跨IP 联动规则（如有）

---

### 1.2 creation-standards.md — 创作规范

```yaml
page_type: "creation-standards"
project_id: "common"
```

> **双作用域页型**：`creation-standards` 既可放系列通用（`common/`），也可放项目专用
> （`{project}/`）。两级页面各自维护独立的来源版本向量与 revision，互不覆盖；同一事项
> 冲突时项目专用优先。

**必须章节：**
- 通用结构规范（页数、段落分配）
- 语言规范（蓝思值、句式、词汇）
- 画面规范（画幅、景别、翻页节奏）
- 叙事规范（情感弧线、翻页悬念）
- 创作红线（列表）

---

### 1.3 quality-rubric.md — 质量评级标准

```yaml
page_type: "quality-rubric"
project_id: "common"
```

**必须章节：**
- 评级体系（S/A/B/待优化）
- 各维度评分标准
- 判定流程

---

### 1.4 market-research.md — 市场调研

```yaml
page_type: "market-research"
project_id: "common"
```

**必须章节：**
- 市场概况
- 竞品分析
- 受众分析
- 差异化策略
- 结论与建议

---

## 二、项目权威页（series/{project}/）

### 2.1 worldview.md — 世界观

```yaml
page_type: "worldview"
```

**必须章节：**
1. **世界观总纲**：一句话概括 + 核心价值主张
2. **世界名称与地理**：世界名称、地理位置、整体氛围
3. **核心场景**：每个微场景的名称、描述、视觉记忆点
4. **单集通用框架**：五段式结构、段落分配
5. **核心物件法则**（如有）
6. **创作红线不变量**：不可违反的设定铁律

**源引用规则：** 每个结构性声明必须附带 `> 来源：{飞书文档标题}` 引用。

---

### 2.2 characters.md — 角色人设

```yaml
page_type: "characters"
```

**必须章节：**
1. **角色总览**：角色列表 + 核心关系
2. **核心角色详情**（每个角色）：
   - 基本档案（名字、物种/身份、年龄、性别）
   - 性格特质
   - 外貌特征
   - **身体语言系统**（表格：情绪状态 / 身体部位 / 动作描述）
   - 关系网络
   - 角色弧光
3. **IP 钩子**：必落地元素列表
4. **命名体系**（如有）
5. **创作边界**：角色不可做的事

---

### 2.3 content-spec.md — 内容规格

```yaml
page_type: "content-spec"
```

**必须章节：**
1. **核心参数**：页数、蓝思范围、每页词数、总词数范围
2. **五段式页分配**（如适用）
3. **格式规范**：表格结构、列定义
4. **定稿分集信息**（表格：集号 / 标题 / 页数 / 蓝思值 / 核心物件 / 金句）

---

### 2.4 corrections.md — 纠正登记册

```yaml
page_type: "corrections"
```

**必须章节：**
1. **强制性禁止条目**：绝不可出现的写法、词汇、结构
2. **身体语言禁止词汇**：白名单制
3. **金句纠正**：被否决的金句模式
4. **画面纠正**：被否决的画面处理方式
5. **迭代历史**：反复出现的错误模式
6. **红线机器可读块**：供 wiki-ingest 的「同步约束清单」步骤重建红线词表

**红线机器可读块模板**（遵循本文件的「机器可读数据块约定」）：

```markdown
<!-- machine-data: redline_terms -->
redline_terms:
  - "tip-tap-tremble"
  - "变勇敢了"
  - "魔法解决一切"
```

约定：
- **只写字面禁用词**，不写整句规则说明——该词表同时用于草稿的正则/计数扫描，长句永远匹配不上。
- 人读的完整规则仍写在「强制性禁止条目」等散文章节，二者互补：散文讲为什么，本块给机器匹配什么。
- 本块缺失时，同步步骤会回退解析禁止类章节中被反引号 / 引号标出的片段，**因此没有本块也能运行**；有本块则更准。
- `corrections-promote` 操作在把用户显式确认固化的红线固化进本文件时，须同时写入散文条目与本块。

---

## 三、项目参考索引与台账（series/{project}/）

### 3.1 references.md — 参考索引

```yaml
page_type: "references"
```

**必须章节：**
1. **定稿脚本索引**（表格：集号 / 标题 / 页数 / 蓝思值 / 核心物件 / 金句 / 源文件）
2. **选题策划存档**（表格：编号 / 核心巧思 / 主题 / 状态）
3. **世界观文档索引**
4. **其他参考文档**

---

### 3.2 creative-feature-ledger.md — 创意特征台账

```yaml
page_type: "creative-feature-ledger"
```

**人读表格列：**
| 集 | 触发类型 | 退缩本体 | 误解机制 | 揭示方式 | 前进动因 | 结局形态 | 金句落点 | 核心物件 | 设定变体 | 来源 |

**机器可读数据块（必须）：**
```markdown
<!-- machine-data: episodes -->
```yaml
- id: "S1"
  title_en: "..."
  fingerprint:
    trigger: "..."
    retreat_form: "..."
    core_object: "..."
    ...
```
```

**否决台账数据块：**
```markdown
<!-- machine-data: rejected_shells -->
```yaml
- status: rejected
  reason: "..."
  aliases: ["..."]
```
```

---

### 3.3 golden-sentence-registry.md — 金句登记册

```yaml
page_type: "golden-sentence-registry"
```

**人读表格列：**
| 集 | 金句 (EN) | 模式类型 | 句式结构 | 情感基调 | 来源 |

**金句规则（必须章节）：**
- 禁止的句式/词汇
- 情感基调要求

---

### 3.4 prop-registry.md — 道具登记册

```yaml
page_type: "prop-registry"
```

**人读表格列：**
| 道具 | 使用集数 | 类型 | 可复用？ | 说明 | 来源 |

**机器可读数据块（必须）：**
```markdown
<!-- machine-data: props -->
```yaml
- name: "..."
  episodes: ["S1", "S3"]
  type: "salient"
  reusable: true
  status: "ALLOWED_NON_SALIENT"
```
```

---

### 3.5 story-fingerprint-spec.md — 选题指纹与门禁规格

```yaml
page_type: "story-fingerprint-spec"
```

**必须章节：**
1. **核心情感机制**：操作化定义 + core_test
2. **15维指纹维度定义**（machine-data: dimensions）
3. **Hamming 阈值与禁止组合对**（machine-data: thresholds）
4. **质量门禁**（machine-data: quality_gates）
5. **批次多样性约束**（machine-data: batch_diversity）
6. **禁用词与禁止写法**（machine-data: banned_terms）
7. **尺度接触规则**（machine-data: scale_contact）

---

## 四、系统文件（根目录）

### 4.1 index.md — 知识库导航索引

```yaml
title: "知识库导航索引"
page_type: "index"
series_id: ""
project_id: ""
```

**必须章节：**
1. 领域知识 section
2. 系列通用知识 section（列出所有 common/ 文件及描述）
3. 项目知识 section（按项目列出所有文件，表格：文件/类型/描述）

---

### 4.2 log.md — 知识库变更日志

```yaml
title: "知识库变更日志"
page_type: "log"
series_id: ""
project_id: ""
```

**格式：** 仅追加。每条变更记录格式：
```
## YYYY-MM-DD
- 操作（新增/更新/废弃）：页面路径 — 简述
```

---

## 五、内容合成质量要求

1. **每个结构性声明必须引用源飞书文档**（使用 `> 来源：{飞书标题}` 或 `> 原文引用："..."`）
2. **多个文档冲突时登记到 corrections.md**，不自行选择
3. **台账类文件的机器可读数据块必须完整**（供脚本消费）
4. **人读格式与机器可读数据块须同步维护**——修改任一侧须同步更新另一侧
5. **定稿脚本缺失字段标注"未知"**，不阻塞，不臆造

---

## 六、结构化数据直通模板

当文档 corpus 中包含 xlsx 衍生的 Markdown 表格时，LLM 合成阶段适用以下补充规则，以确保表格数据不因归纳总结而丢失精度。

### 6.1 数据语义 → 目标文件映射

Agent 应根据表格的列语义，将 xlsx 数据分配到对应的输出文件。一张多用途 xlsx 表格可以拆分到多个输出文件。

| xlsx 表格语义 | 目标文件 | 对应模板章节 |
|---|---|---|
| IP 系列概览（IP 名称 / 主角 / 世界观 / 核心主题 / 出版规格 / 状态） | `ip-overview.md` | IP 矩阵（表格） |
| 角色属性（姓名 / 物种 / 性格特质 / 外貌特征 / 关系） | `characters.md` | 核心角色详情 + 关系网络 |
| 角色身体语言（情绪状态 / 身体部位 / 动作描述） | `characters.md` | 身体语言系统（表格） |
| 出版规格（页数 / 蓝思范围 / 每页词数 / 总词数范围 / 画幅） | `content-spec.md` | 核心参数 + 定稿分集信息（表格） |
| 脚本元数据（集号 / 标题 / 页数 / 蓝思值 / 核心物件 / 金句） | `references.md` | 定稿脚本索引（表格） |
| 选题策划（编号 / 核心巧思 / 主题 / 状态） | `references.md` | 选题策划存档（表格） |
| 禁止条目 / 创作红线 | `corrections.md` | 强制性禁止条目 |
| 故事指纹维度（触发类型 / 退缩本体 / 误解机制 / …） | `creative-feature-ledger.md` | 人读表格 + machine-data YAML 块 |
| 金句数据（原文 / 模式类型 / 句式结构 / 情感基调 / 来源） | `golden-sentence-registry.md` | 人读表格 |
| 道具数据（道具名 / 使用集数 / 类型 / 可复用 / 说明） | `prop-registry.md` | 人读表格 + machine-data YAML 块 |
| 参数 / 门禁数值（阈值、多样性约束、禁用词列表） | `content-spec.md` 或 `story-fingerprint-spec.md` | 对应数值字段 + machine-data YAML 块 |

### 6.2 完整性规则

1. **行不可少**：xlsx 数据行 → 最终输出中至少一条对应记录。允许一对一或一对多（同一行拆分到多个文件），禁止多对一（多行合并为一行）。

2. **列不可丢**：xlsx 中的每一列必须在最终输出的某个文件中有对应字段或表列。若某列在目标模板中无直接对应项，应在最相关的文件中新增列，或者在源引用中注明"保留备用"。

3. **值不可改**：数据值（数字、字符串、枚举）必须原样保留。不得"优化"数值精度、"规范化"命名、或"翻译"专有名词。如果源数据中存在明显笔误（如拼写错误），不得自行修正——应在 `corrections.md` 中登记。

4. **表格形态必须保持**：数据必须以 Markdown 表格格式出现在最终文件中。堆叠成文本段落、折叠进 YAML 块（除非已有 `<!-- machine-data: -->` 锚点明确要求 YAML）、或改写为列表，均不满足要求。

5. **machine-data 块优先**：如果目标模板定义了 `<!-- machine-data: -->` YAML 块（如 `creative-feature-ledger.md` 的 `machine-data: episodes`、`prop-registry.md` 的 `machine-data: props`、`story-fingerprint-spec.md` 的七个 machine-data 块），表格数据应同时填充人读表格和 machine-data YAML 块。两侧必须同步一致。

### 6.3 验证清单（Step 6 完成后自查）

- [ ] 来自 xlsx 的每一行在至少一个输出文件中可找到对应行
- [ ] 每一列在至少一个输出文件中有对应列
- [ ] 数值数据未发生变化（与源 xlsx 中的值逐项比对）
- [ ] 表格形态得以保持（Markdown 表格，不是段落文字）
- [ ] 若有 machine-data 块，人读表格与 machine-data YAML 块的数据一致
- [ ] 每个从 xlsx 迁移的表格带有 `> 结构化数据来源：` 标注
