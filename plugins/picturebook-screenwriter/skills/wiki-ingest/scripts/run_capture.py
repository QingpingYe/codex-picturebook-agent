#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""输出捕获标准姿势（wiki-ingest Step 0）——命令执行 → stdout+stderr 合并落盘 → 打印 EXIT 与摘要。

CLI:
  python run_capture.py --cmd "<命令>" [--out <名>] [--workdir <dir>] [--show N] [--echo]

- 输出落 <workdir>/feishu_sync_tmp/_capture/<out>.txt（原始字节写盘，规避控制台乱码）
- 复杂 bash 命令用 --cmd 'bash -c "..."' 包装
- 退出码透传（EXIT=<n> 与进程退出码一致），供"同一条命令内 && rm"式联动
"""
import argparse
import os
import subprocess
import sys

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def run(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description="命令输出捕获包装器（输出落盘 + 摘要打印）")
    ap.add_argument("--cmd", required=True, help="要执行的命令（原样交给 shell）")
    ap.add_argument("--out", default="_out", help="落盘文件名（自动补 .txt）")
    ap.add_argument("--workdir", default=BASE, help="执行与落盘的工作目录（默认专家包根）")
    ap.add_argument("--show", type=int, default=40, help="摘要打印首/末各 N 行")
    ap.add_argument("--echo", action="store_true", help="stdout 全量打印输出（默认只打摘要）")
    args = ap.parse_args(argv)

    name = args.out if args.out.endswith(".txt") else args.out + ".txt"
    cap_dir = os.path.join(args.workdir, "feishu_sync_tmp", "_capture")
    os.makedirs(cap_dir, exist_ok=True)
    out_path = os.path.join(cap_dir, name)

    p = subprocess.run(args.cmd, shell=True, cwd=args.workdir, capture_output=True)
    raw = p.stdout + p.stderr
    with open(out_path, "wb") as f:
        f.write(raw)

    print(f"EXIT={p.returncode}")
    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()
    if args.echo:
        summary = text
    else:
        n = max(1, args.show)
        parts = lines[:n]
        if len(lines) > 2 * n:
            parts.append(f"... ({len(lines) - 2 * n} 行省略) ...")
        if len(lines) > n:
            parts.extend(lines[-n:])
        summary = "\n".join(parts)
    if summary.strip():
        print(summary)
    print(f"captured: {out_path} ({len(raw)} bytes)")
    return p.returncode


if __name__ == "__main__":
    sys.exit(run())
