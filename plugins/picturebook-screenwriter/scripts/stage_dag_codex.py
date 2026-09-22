#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把阶段 DAG 调度桥接到 Codex multi_agent_v1 工具。"""
from __future__ import annotations

import copy
import json
import sys
from argparse import ArgumentParser

import stage_dag


PLAN_SCHEMA = "pb-dispatch-plan-v2"
LEAD_OWNER = "picturebook-screenwriter-team-lead"
LEAD_OWNERS = {LEAD_OWNER}
LEAD_OWNED_STAGE_IDS = {
    "confirmation_gate",
    "asset_confirmation",
    "asset_final_confirmation",
}
CONFIRMATION_OUTCOMES = [
    "approved",
    "revision_requested",
    "cancelled",
]


def load_manifest(path):
    """读取 JSON run manifest 并返回调度器校验后的深拷贝。"""
    with open(path, encoding="utf-8") as fh:
        manifest = json.load(fh)
    return stage_dag.validate_manifest(manifest)


def _stage_map(manifest):
    return {stage["stage_id"]: stage for stage in manifest["stages"]}


def _plan_stage(manifest, stage):
    return {
        "stage_id": stage["stage_id"],
        "assignee": stage["assignee"],
        "task_envelope": format_task_envelope(manifest, stage),
    }


def _plan_batches(manifest):
    stages = _stage_map(manifest)
    plan_batches = []
    for batch in stage_dag.next_batches(manifest):
        actionable = [
            stage_id
            for stage_id in batch
            if (
                stages[stage_id]["assignee"] not in LEAD_OWNERS
                and stages[stage_id]["gate"] != "confirmation"
                and stage_id not in LEAD_OWNED_STAGE_IDS
            )
        ]
        if actionable:
            plan_batches.append({
                "batch_index": len(plan_batches),
                "stages": [
                    _plan_stage(manifest, stages[stage_id])
                    for stage_id in actionable
                ],
            })
    return plan_batches


def _empty_dispatch_plan(validated):
    return {
        "schema_version": PLAN_SCHEMA,
        "run_id": validated["run_id"],
        "iteration": validated["iteration"],
        "status": "ready",
        "lead_actions": [],
        "next_batches": [],
        "waiting_for": [],
        "deferred_stages": [],
        "decision_required": None,
        "blocked_reasons": [],
        "follow_up_action": None,
    }


def build_dispatch_plan(manifest):
    """构造可直接交给 Codex multi_agent_v1 的结构化派发计划。"""
    validated = stage_dag.validate_manifest(manifest)
    stages = _stage_map(validated)
    plan = _empty_dispatch_plan(validated)

    if validated["status"] in {"completed", "cancelled"}:
        plan["status"] = "run_completed"
        plan["run_outcome"] = validated["outcome"]
        if validated["outcome"] == "revision_requested":
            plan["follow_up_action"] = {
                "action": "create_revision_run",
                "parent_run_id": validated["run_id"],
            }
        return plan

    decisions = stage_dag.resolve_stage_decisions(validated)
    ready = [
        item.stage_id
        for item in decisions
        if item.decision == "ready"
    ]

    for stage_id in ready:
        stage = stages[stage_id]
        if (
            stage["gate"] == "confirmation"
            or stage_id in LEAD_OWNED_STAGE_IDS
        ):
            plan["status"] = "waiting_for_user"
            plan["decision_required"] = {
                "stage_id": stage["stage_id"],
                "owner": LEAD_OWNER,
                "allowed_outcomes": list(CONFIRMATION_OUTCOMES),
            }
            return plan

    plan["lead_actions"] = [
        _plan_stage(validated, stages[stage_id])
        for stage_id in ready
        if stages[stage_id]["assignee"] in LEAD_OWNERS
    ]
    plan["next_batches"] = _plan_batches(validated)
    plan["blocked_reasons"] = [
        {
            "stage_id": item.stage_id,
            "reason": item.reason,
        }
        for item in decisions
        if item.decision == "block"
    ]

    for stage in validated["stages"]:
        when = stage.get("when")
        if (
            when is None
            or stage["status"] not in {"pending", "ready"}
        ):
            continue
        decision = next(
            (
                item
                for item in decisions
                if item.stage_id == stage["stage_id"]
            ),
            None,
        )
        if decision is not None and decision.decision == "ready":
            continue
        reason = f"awaiting {when['stage_id']} outcome"
        if decision is not None and decision.decision == "skip":
            reason = (
                f"condition outcome does not match {when['outcome']}"
            )
        plan["deferred_stages"].append({
            "stage_id": stage["stage_id"],
            "reason": reason,
        })
    return plan


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
        "inputs": {
            "input_refs": list(target["input_refs"]),
            "run_context": {
                "root_run_id": validated["root_run_id"],
                "revision_of_run_id": validated["revision_of_run_id"],
                "iteration": validated["iteration"],
                "revision_feedback": copy.deepcopy(
                    validated["revision_feedback"]
                ),
                "source_artifact_ref": validated["source_artifact_ref"],
            },
        },
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
    plan = build_dispatch_plan(validated)
    plan["mode"] = "sequential"
    return plan


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
