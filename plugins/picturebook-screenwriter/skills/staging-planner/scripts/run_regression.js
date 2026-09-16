#!/usr/bin/env node
"use strict";

/**
 * run_regression.js — project_scene_map.js 回归测试
 *
 * 用法：node run_regression.js
 *
 * 读取同目录 cases.jsonl（每行一个 JSON 用例对象 {name, input, expect}），
 * 逐条调用 project_scene_map 纯函数，比对 expect 中声明的关键字段。
 * expect 支持子集断言：对象逐键递归比对（实际输出可含未断言的额外字段），
 * 数组按等长逐项递归比对。
 *
 * expect_fail: true 的用例走 CLI 路径（子进程 node project_scene_map.js，stdin 传入 input），
 * 断言进程非零退出——用于锁定入口层输入校验（如非有限坐标拦截），不比对输出。
 *
 * 全部 PASS → exit 0；任一 FAIL → exit 1。不写任何文件。
 */

const fs = require("fs");
const path = require("path");
const { execFileSync } = require("child_process");
const { projectSceneMap } = require("./project_scene_map.js");

function subsetMatch(actual, expected, keyPath, mismatches) {
  if (expected === null || typeof expected !== "object") {
    if (actual !== expected) {
      mismatches.push(`${keyPath}: 期望 ${JSON.stringify(expected)}，实际 ${JSON.stringify(actual)}`);
    }
    return;
  }
  if (Array.isArray(expected)) {
    if (!Array.isArray(actual) || actual.length !== expected.length) {
      mismatches.push(
        `${keyPath}: 数组长度不符（期望 ${expected.length} 项，实际 ${Array.isArray(actual) ? actual.length : typeof actual}）`
      );
      return;
    }
    expected.forEach((item, i) => subsetMatch(actual[i], item, `${keyPath}[${i}]`, mismatches));
    return;
  }
  if (actual === null || typeof actual !== "object" || Array.isArray(actual)) {
    mismatches.push(`${keyPath}: 期望对象，实际 ${JSON.stringify(actual)}`);
    return;
  }
  for (const key of Object.keys(expected)) {
    if (!(key in actual)) {
      mismatches.push(`${keyPath}.${key}: 字段缺失`);
      continue;
    }
    subsetMatch(actual[key], expected[key], `${keyPath}.${key}`, mismatches);
  }
}

function main() {
  const casesPath = path.join(__dirname, "cases.jsonl");
  const lines = fs.readFileSync(casesPath, "utf8").split(/\r?\n/).filter((l) => l.trim() !== "");
  let pass = 0;
  let fail = 0;

  for (let i = 0; i < lines.length; i++) {
    let tc;
    try {
      tc = JSON.parse(lines[i]);
    } catch (err) {
      fail++;
      console.log(`FAIL 第 ${i + 1} 行（用例 JSON 解析失败）：${err.message}`);
      continue;
    }
    try {
      if (tc.expect_fail === true) {
        // CLI 路径：子进程跑入口层，断言非零退出（如坐标校验拦截）
        const scriptPath = path.join(__dirname, "project_scene_map.js");
        try {
          execFileSync(process.execPath, [scriptPath], {
            input: JSON.stringify(tc.input),
            encoding: "utf8",
          });
          fail++;
          console.log(`FAIL ${tc.name}（期望 CLI 非零退出，实际退出 0）`);
        } catch (err) {
          if (typeof err.status === "number" && err.status !== 0) {
            pass++;
            console.log(`PASS ${tc.name}（CLI 退出码 ${err.status}）`);
          } else {
            fail++;
            console.log(`FAIL ${tc.name}（CLI 启动异常：${err.message}）`);
          }
        }
        continue;
      }
      const actual = projectSceneMap(tc.input);
      const mismatches = [];
      subsetMatch(actual, tc.expect, "$", mismatches);
      if (mismatches.length > 0) {
        fail++;
        console.log(`FAIL ${tc.name}`);
        for (const m of mismatches) console.log(`      ${m}`);
      } else {
        pass++;
        console.log(`PASS ${tc.name}`);
      }
    } catch (err) {
      fail++;
      console.log(`FAIL ${tc.name}（投影异常：${err.message}）`);
    }
  }

  console.log("");
  console.log(`回归测试汇总：共 ${lines.length} 条用例，${pass} PASS / ${fail} FAIL`);
  if (fail > 0) {
    console.log("存在失败用例，退出码 1。");
    process.exit(1);
  }
  console.log("全部用例通过。");
}

main();
