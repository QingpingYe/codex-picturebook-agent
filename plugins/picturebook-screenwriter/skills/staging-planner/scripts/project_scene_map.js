#!/usr/bin/env node
"use strict";

/**
 * project_scene_map.js — 场景俯视图（空间账本）确定性投影脚本
 *
 * 把世界系空间账本（scene_map）投影到给定机位的画面坐标系：
 *   - 地标 → 帧内走向（bearing/from/to）+ 帧向铁律（frame_laws）文本
 *   - 角色 → 画面方位（screen_position）+ 相对镜头朝向（facing_relative）
 *   - 角色 × 地标 → 相对方位关系（char_relations）
 *
 * 世界系约定（与 skills/staging-planner/SKILL.md 的「scene_map schema」一致）：
 *   北 = 纸面上方（+y），东 = 右（+x）；方位仅用八方位 N/NE/E/SE/S/SW/W/NW（顺时针 0–7 编码）；
 *   账本只存世界系，禁止「画面左/右」——帧内方位一律由本脚本投影推导；
 *   坐标是定性尺度（谁在谁哪边），不做米制。
 *
 * 投影规则与 staging-planner SKILL.md 的「确定性投影」规则表一致，规则表是权威定义。
 *
 * 用法：
 *   node project_scene_map.js < input.json
 *   echo '{...}' | node project_scene_map.js
 * stdin 读一个 JSON 对象（每次调用处理一页 / 一个机位），stdout 写投影结果 JSON。
 * **不写任何文件**；失败时非零退出并向 stderr 输出错误信息。
 *
 * 纯函数经 module.exports 导出（projectSceneMap 等），供 run_regression.js 回归测试 require；
 * CLI 入口用 if (require.main === module) 包裹（与仓库内 generate.js 先例一致）。
 * 仅依赖 Node 内置模块（fs），零外部依赖。
 *
 * 距离档位为定性阈值（非米制），可调：
 *   |d| < 1.5 前景；1.5 ≤ |d| < 4 中景；≥ 4 后景。
 */

const fs = require("fs");

// ---- 八方位编码（顺时针）：N=0, NE=1, E=2, SE=3, S=4, SW=5, W=6, NW=7 ----
const DIR_CODE = { N: 0, NE: 1, E: 2, SE: 3, S: 4, SW: 5, W: 6, NW: 7 };
// 方位中文（用于 char_relations 的「位于{地标}{方位}」文本，如「位于河流以南」）
const CODE_NAME_ZH = ["以北", "以东北", "以东", "以东南", "以南", "以西南", "以西", "以西北"];

// ---- 距离档位阈值（定性，可调，见文件头）----
const NEAR_DIST = 1.5;
const FAR_DIST = 4;

// ---- 地标帧内走向规则表：rel = (L - C + 8) % 8，L = 地标延伸方向（orientation.to）编码，C = 相机朝向编码 ----
const FRAME_ORIENTATION_TABLE = {
  0: { bearing: "vertical", from: "bottom", to: "top",
       law: (name) => `${name}从画面前景向背景纵向延伸` },
  4: { bearing: "vertical", from: "top", to: "bottom",
       law: (name) => `${name}从画面背景向前景纵向延伸（迎向镜头）` },
  2: { bearing: "horizontal", from: "left", to: "right",
       law: (name) => `${name}在画面中从左到右横向横贯画面` },
  6: { bearing: "horizontal", from: "right", to: "left",
       law: (name) => `${name}在画面中从右到左横向横贯画面` },
  1: { bearing: "diagonal", from: "bottom-left", to: "top-right",
       law: (name) => `${name}在画面中沿对角线延伸（从左下斜向右上）` },
  3: { bearing: "diagonal", from: "top-left", to: "bottom-right",
       law: (name) => `${name}在画面中沿对角线延伸（从左上斜向右下）` },
  5: { bearing: "diagonal", from: "top-right", to: "bottom-left",
       law: (name) => `${name}在画面中沿对角线延伸（从右上斜向左下）` },
  7: { bearing: "diagonal", from: "bottom-right", to: "top-left",
       law: (name) => `${name}在画面中沿对角线延伸（从右下斜向左上）` },
};

// ---- 角色画面方位：rel_pos = (bearing(角色位置 − 相机位置) − C + 8) % 8 ----
const CHAR_POSITION_LABEL = {
  0: "画面中上部",
  1: "画面右侧",
  2: "画面右侧",
  3: "画面下侧",
  5: "画面左侧",
  6: "画面左侧",
  7: "画面左上方",
};
// rel_pos = 4 为相机身后 → 不入画（in_frame=false），不产出 screen_position

// ---- 角色相对镜头朝向：rel_f = (facing − C + 8) % 8 ----
const CHAR_FACING_LABEL = {
  0: "背对镜头",
  1: "面朝画面右上方（3/4 背对镜头）",
  2: "面朝画面右侧",
  3: "面朝画面右下方（3/4 面向镜头）",
  4: "面朝镜头",
  5: "面朝画面左下方（3/4 面向镜头）",
  6: "面朝画面左侧",
  7: "面朝画面左上方（3/4 背对镜头）",
};

function assertDir(code, what) {
  if (DIR_CODE[code] === undefined) {
    throw new Error(`${what} 含非法八方位编码：${JSON.stringify(code)}（合法值：N/NE/E/SE/S/SW/W/NW）`);
  }
}

/** 方位角编码：angleDeg = atan2(dx, dy) * 180 / PI（0°=北，顺时针为正）。 */
function bearingCode(dx, dy) {
  const angleDeg = (Math.atan2(dx, dy) * 180) / Math.PI;
  return ((Math.round(angleDeg / 45)) % 8 + 8) % 8;
}

/** origin 看向 target 的方位角编码。 */
function bearingFrom(origin, target) {
  return bearingCode(target.x - origin.x, target.y - origin.y);
}

/** 视锥检查：bearing 落在 {C-2 … C+2} mod 8 扇区（含边界）→ 入画。 */
function inSector(cameraCode, bearing) {
  const rel = ((bearing - cameraCode) % 8 + 8) % 8;
  return rel <= 2 || rel >= 6;
}

function distanceTier(dist) {
  if (dist < NEAR_DIST) return "前景";
  if (dist < FAR_DIST) return "中景";
  return "后景";
}

/** 地标参照点：line 取两端点 + 中点（任一入扇区即入画）；area/point 取 anchor（缺失即抛错——禁止静默回退 {0,0} 伪造 char_relations）。 */
function landmarkRefPoints(landmark) {
  if (landmark.type === "line" && Array.isArray(landmark.extent) && landmark.extent.length > 0) {
    const pts = landmark.extent.map((p) => ({ x: p.x, y: p.y }));
    if (pts.length > 1) {
      const mid = { x: (pts[0].x + pts[1].x) / 2, y: (pts[0].y + pts[1].y) / 2 };
      return { inFramePoints: [...pts, mid], relPoint: mid };
    }
    return { inFramePoints: pts, relPoint: pts[0] };
  }
  if (!landmark.anchor || typeof landmark.anchor !== "object") {
    throw new Error(
      `地标 ${landmark.id || landmark.name || "未命名"}（type=${landmark.type || "未标注"}）缺少 anchor 定位点（area/point 地标用 anchor 定位，line 地标用 extent）`
    );
  }
  return { inFramePoints: [landmark.anchor], relPoint: landmark.anchor };
}

function projectLandmark(landmark, cameraCode, cameraPos, charsInFrame) {
  // 与 characters[].facing 的 assertDir 对称：地标走向编码非法（如 LLM 转换附录产出「北」/「North」）即抛错，
  // 不做静默查表落空——否则 rel=NaN 会无任何报错地丢弃 frame_law。
  if (landmark.type === "line" && landmark.orientation) {
    assertDir(landmark.orientation.to, `landmarks[${landmark.id || landmark.name || "未命名"}].orientation.to`);
    assertDir(landmark.orientation.from, `landmarks[${landmark.id || landmark.name || "未命名"}].orientation.from`);
  }
  const ref = landmarkRefPoints(landmark);
  const visible = ref.inFramePoints.some((p) => inSector(cameraCode, bearingFrom(cameraPos, p)));
  const out = { id: landmark.id, name: landmark.name, in_frame: visible };
  let law = null;
  if (visible && landmark.type === "line" && landmark.orientation) {
    const L = DIR_CODE[landmark.orientation.to];
    const rel = ((L - cameraCode) % 8 + 8) % 8;
    const proj = FRAME_ORIENTATION_TABLE[rel];
    if (proj) {
      out.frame_orientation = { bearing: proj.bearing, from: proj.from, to: proj.to };
      law = proj.law(landmark.name);
    }
  }
  if (visible && charsInFrame.length > 0) {
    // 方位取角色相对地标的 bearing（角色在地标的哪一侧），如「角色A 位于河流以南」
    out.char_relations = charsInFrame.map((c) => {
      const dirCode = bearingFrom(ref.relPoint, c.position);
      return `${c.name} 位于${landmark.name}${CODE_NAME_ZH[dirCode]}`;
    });
  }
  return { entry: out, law };
}

function projectCharacter(character, cameraCode, cameraPos) {
  const bearing = bearingFrom(cameraPos, character.position);
  const relPos = ((bearing - cameraCode) % 8 + 8) % 8;
  if (relPos === 4) {
    return { name: character.name, in_frame: false }; // 相机身后，不入画
  }
  const relF = ((DIR_CODE[character.facing] - cameraCode) % 8 + 8) % 8;
  const dist = Math.hypot(
    character.position.x - cameraPos.x,
    character.position.y - cameraPos.y
  );
  return {
    name: character.name,
    in_frame: true,
    screen_position: `${CHAR_POSITION_LABEL[relPos]}·${distanceTier(dist)}`,
    facing_relative: CHAR_FACING_LABEL[relF],
  };
}

/** 核心纯函数：输入 {landmarks, characters, camera} → 投影结果。 */
function projectSceneMap(input) {
  const camera = input.camera;
  assertDir(camera.facing, "camera.facing");
  const cameraCode = DIR_CODE[camera.facing];
  const cameraPos = camera.position;
  const rawCharacters = input.characters || [];
  const characters = rawCharacters.map((c) => {
    assertDir(c.facing, `characters[${c.name}].facing`);
    return projectCharacter(c, cameraCode, cameraPos);
  });
  // char_relations 需要世界系位置，用原始角色（未投影）过滤入画者
  const charsInFrameRaw = rawCharacters.filter((_, i) => characters[i].in_frame);
  const frame_laws = [];
  const landmarks = (input.landmarks || []).map((lm) => {
    const { entry, law } = projectLandmark(lm, cameraCode, cameraPos, charsInFrameRaw);
    if (law) frame_laws.push(law);
    return entry;
  });
  return { landmarks, characters, frame_laws };
}

module.exports = {
  projectSceneMap,
  projectLandmark,
  projectCharacter,
  bearingFrom,
  bearingCode,
  inSector,
  distanceTier,
  DIR_CODE,
  CODE_NAME_ZH,
  FRAME_ORIENTATION_TABLE,
  CHAR_POSITION_LABEL,
  CHAR_FACING_LABEL,
  NEAR_DIST,
  FAR_DIST,
};

// ---- CLI 入口：stdin 读 JSON → stdout 写 JSON，不写任何文件 ----
// CLI 层额外做坐标数值校验（纯函数层保持宽松）：x/y 须为有限数值（JSON 的 1e999 会解析成 Infinity，同样拦截）。
function isFiniteNum(v) {
  return typeof v === "number" && Number.isFinite(v);
}

function assertFiniteCoords(pt, what) {
  if (!pt || typeof pt !== "object" || !isFiniteNum(pt.x) || !isFiniteNum(pt.y)) {
    throw new Error(`${what} 坐标含非有限数值（x/y 须为有限数字）`);
  }
}

function validateCLICoords(input) {
  assertFiniteCoords(input.camera.position, "camera.position");
  (input.characters || []).forEach((c, i) => {
    assertFiniteCoords(c.position, `characters[${i}](${c.name || "未命名"}).position`);
  });
  (input.landmarks || []).forEach((lm, i) => {
    const tag = `landmarks[${i}](${lm.id || lm.name || "未命名"})`;
    if (lm.type === "line" && Array.isArray(lm.extent) && lm.extent.length > 0) {
      lm.extent.forEach((p, j) => assertFiniteCoords(p, `${tag}.extent[${j}]`));
    } else if (lm.anchor !== undefined) {
      assertFiniteCoords(lm.anchor, `${tag}.anchor`);
    }
  });
}

if (require.main === module) {
  let raw = "";
  try {
    raw = fs.readFileSync(0, "utf8");
  } catch (err) {
    process.stderr.write(`project_scene_map: 读取 stdin 失败：${err.message}\n`);
    process.exit(1);
  }
  try {
    const input = JSON.parse(raw);
    if (!input || typeof input !== "object" || Array.isArray(input)) {
      throw new Error("输入须为 JSON 对象");
    }
    if (!input.camera) {
      throw new Error("输入缺少 camera 字段");
    }
    if (!input.camera.position) {
      throw new Error("camera.position 缺失");
    }
    validateCLICoords(input);
    const result = projectSceneMap(input);
    process.stdout.write(JSON.stringify(result, null, 2) + "\n");
  } catch (err) {
    process.stderr.write(`project_scene_map: ${err.message}\n`);
    process.exit(1);
  }
}
