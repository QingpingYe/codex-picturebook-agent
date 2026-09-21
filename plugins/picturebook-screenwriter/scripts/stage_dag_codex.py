#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把阶段 DAG 调度桥接到 Codex multi_agent_v1 工具。"""
from __future__ import annotations

import json
import sys
from argparse import ArgumentParser

import stage_dag


def load_manifest(path):
    """读取 JSON run manifest 并返回调度器校验后的深拷贝。"""
    with open(path, encoding="utf-8") as fh:
        manifest = json.load(fh)
    return stage_dag.validate_manifest(manifest)


def _stage_map(manifest):
    return {stage["stage_id"]: stage for stage in manifest["stages"]}


def _batch_stages(manifest, stage_ids):
    stages = _stage_map(manifest)
    return [
        {"stage_id": stage_id, "assignee": stages[stage_id]["assignee"]}
        for stage_id in stage_ids
    ]


def build_dispatch_plan(manifest):
    """构造可直接交给 Codex multi_agent_v1 的结构化派发计划。"""
    validated = stage_dag.validate_manifest(manifest)
    batches = stage_dag.parallel_batches(validated)
    stages = _stage_map(validated)
    plan_batches = [
        {
            "batch_index": index,
            "stages": [
                {
                    "stage_id": stage_id,
                    "assignee": stages[stage_id]["assignee"],
                    "task_envelope": format_task_envelope(
                        validated, stages[stage_id]
                    ),
                }
                for stage_id in batch
            ],
        }
        for index, batch in enumerate(batches)
    ]
    return {
        "batches": plan_batches,
        "total_batches": len(plan_batches),
        "total_stages": sum(len(batch["stages"]) for batch in plan_batches),
        "mode": (
            "parallel"
            if any(len(batch["stages"]) > 1 for batch in plan_batches)
            else "sequential"
        ),
    }


def format_task_envelope(manifest, stage):
    """生成并通过 pb-stage-task-v1 校验的单个阶段任务信封。"""
    validated = stage_dag.validate_manifest(manifest)
    stage_id = stage["stage_id"]
    target = next(
        (item for item in validated["stages"] if item["stage_id"] == stage_id),
        None,
    )
    if target is None:
        raise stage_dag.StageDagError(f"task 引用未知阶段：{stage_id}")

    task = {
        "schema_version": stage_dag.TASK_SCHEMA,
        "run_id": validated["run_id"],
        "task_id": f"{stage_id}-0001",
        "stage_id": stage_id,
        "agent_id": target["assignee"],
        "logical_target": target["assignee"],
        "inputs": {"input_refs": list(target["input_refs"])},
        "constraints": {
            "gate": target["gate"],
            "depends_on": list(target["depends_on"]),
        },
        "resume_context": f"{validated['run_id']}:{stage_id}",
        "output_paths": list(target["output_refs"]),
        "return_channel": "lead",
    }
    return stage_dag.validate_task(task, validated)


def build_sequential_fallback(manifest):
    """构造无多 Agent 工具时的顺序降级计划。"""
    validated = stage_dag.validate_manifest(manifest)
    batches = stage_dag.parallel_batches(validated)
    fallback_batches = [
        {
            "batch_index": index,
            "stages": _batch_stages(validated, batch),
        }
        for index, batch in enumerate(batches)
    ]
    return {
        "batches": fallback_batches,
        "total_batches": len(fallback_batches),
        "total_stages": sum(len(batch["stages"]) for batch in fallback_batches),
        "mode": "sequential",
    }


def run_cli(argv=None):
    """供主编或人工调用的命令行入口。"""
    parser = ArgumentParser(description="Codex 阶段 DAG 调度适配层")
    parser.add_argument("--manifest", help="run manifest JSON 路径")
    parser.add_argument(
        "--action",
        required=True,
        choices=("plan", "fallback"),
    )
    args = parser.parse_args(argv)

    try:
        if not args.manifest:
            parser.error("--manifest 在 plan/fallback 模式下必填")
        manifest = load_manifest(args.manifest)
        if args.action == "plan":
            result = build_dispatch_plan(manifest)
        else:
            result = build_sequential_fallback(manifest)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except stage_dag.StageDagError as exc:
        print("[stage_dag_codex] 清单或调度失败：", file=sys.stderr)
        for error in exc.errors:
            print(f"- {error}", file=sys.stderr)
        return 2
    except (OSError, ValueError) as exc:
        print(f"[stage_dag_codex] 无法读取 manifest：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(run_cli())
