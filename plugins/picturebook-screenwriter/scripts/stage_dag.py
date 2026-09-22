#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""绘本编剧工坊 — 阶段 DAG 调度核心。

本模块只负责清单校验、状态迁移、依赖排序与并行批次规划；不执行任何 Agent，
也不直接写用户产物。所有用户确认仍由主编处理。
"""
from __future__ import annotations

import copy
import json
import os
import sys
from collections import deque
from argparse import ArgumentParser
from dataclasses import dataclass
from typing import Literal


RUN_SCHEMA_V1 = "pb-stage-run-v1"
RUN_SCHEMA = "pb-stage-run-v2"
TASK_SCHEMA = "pb-stage-task-v1"
RESULT_SCHEMA = "pb-stage-result-v1"

RUN_STATUSES = {
    "pending", "running", "blocked", "completed", "failed", "cancelled",
}
RUN_OUTCOMES = {None, "approved", "revision_requested", "cancelled"}
CONFIRMATION_OUTCOMES = {
    "approved", "revision_requested", "cancelled",
}
TERMINAL_STAGE_STATUSES = {"done", "skipped", "failed", "cancelled"}

STAGE_STATUSES = {
    "pending", "ready", "running", "blocked",
    "done", "skipped", "failed", "cancelled",
}
RESULT_STATUSES = {"done", "needs_input", "blocked", "failed", "cancelled"}
REQUEST_TYPES = {
    "request_input", "request_review", "report_event",
    "confirmation_required", "report_done",
}

TRANSITIONS = {
    "pending": {
        "ready", "running", "blocked", "failed", "cancelled", "skipped",
    },
    "ready": {"running", "blocked", "failed", "cancelled", "skipped"},
    "running": {"blocked", "done", "failed", "cancelled"},
    "blocked": {"ready", "running", "failed", "cancelled"},
    "failed": {"ready", "running"},
    "cancelled": set(),
    "done": set(),
    "skipped": set(),
}


@dataclass(frozen=True)
class StageDecision:
    stage_id: str
    decision: Literal["waiting", "ready", "skip", "block"]
    reason: str


class StageDagError(Exception):
    """带结构化错误清单的调度异常。"""

    def __init__(self, errors):
        if isinstance(errors, str):
            errors = [errors]
        self.errors = list(errors)
        super().__init__("；".join(self.errors))


def _require_nonempty_str(value, label, errors):
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{label} 必须是非空字符串")


def _validate_optional_reason(stage, key, index, errors):
    value = stage.get(key)
    if value is not None and (
        not isinstance(value, str) or not value.strip()
    ):
        errors.append(f"stages[{index}].{key} 必须为 null 或非空字符串")


def _validate_manifest_structure(manifest):
    if not isinstance(manifest, dict):
        return ["manifest 顶层必须是对象"]
    errors = []
    schema_version = manifest.get("schema_version")
    if schema_version == RUN_SCHEMA_V1:
        errors.append(
            f"schema_version 是 {RUN_SCHEMA_V1}，请先运行 --action migrate")
    elif schema_version != RUN_SCHEMA:
        errors.append(f"schema_version 必须是 {RUN_SCHEMA}")
    for key in (
        "run_id", "root_run_id", "intent", "artifact_type",
        "mode", "project_root",
    ):
        _require_nonempty_str(manifest.get(key), key, errors)

    iteration = manifest.get("iteration")
    if (
        isinstance(iteration, bool)
        or not isinstance(iteration, int)
        or iteration < 1
    ):
        errors.append("iteration 必须是 >= 1 的整数")

    if manifest.get("status") not in RUN_STATUSES:
        errors.append(f"status 非法：{manifest.get('status')!r}")
    if manifest.get("outcome") not in RUN_OUTCOMES:
        errors.append(f"outcome 非法：{manifest.get('outcome')!r}")
    for key in ("revision_feedback", "stages"):
        if not isinstance(manifest.get(key), list):
            errors.append(f"{key} 必须是数组")

    revision_of = manifest.get("revision_of_run_id")
    if revision_of is not None and not isinstance(revision_of, str):
        errors.append("revision_of_run_id 必须为 null 或字符串")
    source_ref = manifest.get("source_artifact_ref")
    if source_ref is not None and not isinstance(source_ref, str):
        errors.append("source_artifact_ref 必须为 null 或字符串")

    stages = manifest.get("stages")
    is_light = manifest.get("mode") == "light"
    if not isinstance(stages, list):
        errors.append("stages 必须是非空数组")
        return errors
    if not stages and not is_light:
        errors.append("stages 必须是非空数组（mode=light 时允许为空）")
        return errors

    stage_by_id = {
        stage.get("stage_id"): stage
        for stage in stages
        if isinstance(stage, dict)
        and isinstance(stage.get("stage_id"), str)
    }
    ids = set()
    for index, stage in enumerate(stages):
        if not isinstance(stage, dict):
            errors.append(f"stages[{index}] 必须是对象")
            continue
        sid = stage.get("stage_id")
        _require_nonempty_str(sid, f"stages[{index}].stage_id", errors)
        if isinstance(sid, str) and sid:
            if sid in ids:
                errors.append(f"stage_id 重复：{sid}")
            ids.add(sid)
        _require_nonempty_str(
            stage.get("assignee"), f"stages[{index}].assignee", errors)
        deps = stage.get("depends_on")
        if not isinstance(deps, list):
            errors.append(f"stages[{index}].depends_on 必须是数组")
        status = stage.get("status")
        if status not in STAGE_STATUSES:
            errors.append(f"stages[{index}].status 非法：{status!r}")
        outcome = stage.get("outcome")
        if stage.get("gate") == "confirmation":
            if (
                outcome is not None
                and (
                    not isinstance(outcome, str)
                    or outcome not in CONFIRMATION_OUTCOMES
                )
            ):
                errors.append(
                    f"stages[{index}].outcome 非法：{outcome!r}")
        elif outcome is not None:
            errors.append(f"stages[{index}].outcome 必须为 null")
        _require_nonempty_str(stage.get("gate"), f"stages[{index}].gate", errors)
        when = stage.get("when")
        if when is not None:
            if not isinstance(when, dict) or set(when) != {"stage_id", "outcome"}:
                errors.append(
                    f"stages[{index}].when 必须只包含 stage_id 和 outcome")
            else:
                _require_nonempty_str(
                    when.get("stage_id"), f"stages[{index}].when.stage_id", errors)
                when_stage_id = when.get("stage_id")
                when_outcome = when.get("outcome")
                if (
                    not isinstance(when_outcome, str)
                    or when_outcome not in CONFIRMATION_OUTCOMES
                ):
                    errors.append(
                        f"stages[{index}].when.outcome 非法："
                        f"{when_outcome!r}")
                condition_stage = (
                    stage_by_id.get(when_stage_id)
                    if isinstance(when_stage_id, str)
                    else None
                )
                if (
                    condition_stage is None
                    or condition_stage.get("gate") != "confirmation"
                ):
                    errors.append(
                        f"stages[{index}].when.stage_id 必须引用"
                        " confirmation gate")
                deps = stage.get("depends_on")
                if (
                    isinstance(deps, list)
                    and when_stage_id not in deps
                ):
                    errors.append(
                        f"stages[{index}].when.stage_id 必须是直接依赖")
        for key in ("input_refs", "output_refs"):
            if not isinstance(stage.get(key), list):
                errors.append(f"stages[{index}].{key} 必须是数组")
        _validate_optional_reason(stage, "skip_reason", index, errors)
        _validate_optional_reason(stage, "blocked_reason", index, errors)
    return errors


def _find_dependency_errors(stages):
    ids = {s.get("stage_id") for s in stages if isinstance(s, dict)}
    errors = []
    for stage in stages:
        if not isinstance(stage, dict):
            continue
        sid = stage.get("stage_id")
        for dep in stage.get("depends_on") or []:
            if dep not in ids:
                errors.append(f"stage {sid} 依赖未知的 stage：{dep}")
    return errors


def _topological_order(stages):
    ids = {s["stage_id"] for s in stages}
    dependents = {sid: [] for sid in ids}
    indegree = {sid: 0 for sid in ids}
    for stage in stages:
        sid = stage["stage_id"]
        for dep in stage["depends_on"]:
            dependents[dep].append(sid)
            indegree[sid] += 1

    queue = deque(sorted(sid for sid, degree in indegree.items() if degree == 0))
    order = []
    while queue:
        sid = queue.popleft()
        order.append(sid)
        for dependent in dependents[sid]:
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                queue.append(dependent)

    if len(order) != len(ids):
        remaining = ", ".join(sorted(ids - set(order)))
        raise StageDagError(f"阶段依赖存在循环：{remaining}")
    return order


def validate_manifest(manifest):
    """校验并返回 run manifest 的深拷贝。"""
    errors = _validate_manifest_structure(manifest)
    if errors:
        raise StageDagError(errors)
    stages = manifest["stages"]
    errors.extend(_find_dependency_errors(stages))
    if errors:
        raise StageDagError(errors)
    _topological_order(stages)
    return copy.deepcopy(manifest)


_V1_CREATION_STAGE_IDS = {
    "session_init", "brief_gate", "knowledge_load", "creation_delegate",
    "preflight", "collision_check", "qa", "qa_synthesis",
    "confirmation_gate", "revision_loop", "persistence", "knowledge_reminder",
}
_REQUIRED_V1_STAGE_IDS = _V1_CREATION_STAGE_IDS - {"revision_loop"}


def _validate_v1_manifest_shape(manifest):
    errors = []
    for key in ("run_id", "intent", "artifact_type", "mode", "project_root"):
        _require_nonempty_str(manifest.get(key), f"v1 {key}", errors)

    stages = manifest.get("stages")
    if not isinstance(stages, list):
        errors.append("v1 stages 必须是数组")
        return errors

    seen_stage_ids = set()
    for index, stage in enumerate(stages):
        if not isinstance(stage, dict):
            errors.append(f"v1 stages[{index}] 必须是对象")
            continue

        stage_id = stage.get("stage_id")
        _require_nonempty_str(
            stage_id, f"v1 stages[{index}].stage_id", errors)
        if isinstance(stage_id, str) and stage_id:
            if stage_id in seen_stage_ids:
                errors.append(f"v1 stage_id 重复：{stage_id}")
            seen_stage_ids.add(stage_id)

        _require_nonempty_str(
            stage.get("assignee"), f"v1 stages[{index}].assignee", errors)
        _require_nonempty_str(
            stage.get("gate"), f"v1 stages[{index}].gate", errors)

        deps = stage.get("depends_on")
        if not isinstance(deps, list):
            errors.append(f"v1 stages[{index}].depends_on 必须是数组")
        else:
            for dep_index, dep in enumerate(deps):
                _require_nonempty_str(
                    dep,
                    f"v1 stages[{index}].depends_on[{dep_index}]",
                    errors,
                )

        status = stage.get("status")
        if not isinstance(status, str) or status not in STAGE_STATUSES:
            errors.append(f"v1 stages[{index}].status 非法：{status!r}")

        outcome = stage.get("outcome")
        if (
            outcome is not None
            and (
                not isinstance(outcome, str)
                or outcome not in CONFIRMATION_OUTCOMES
            )
        ):
            errors.append(f"v1 stages[{index}].outcome 非法：{outcome!r}")

        for key in ("input_refs", "output_refs"):
            if not isinstance(stage.get(key), list):
                errors.append(f"v1 stages[{index}].{key} 必须是数组")

    return errors


def migrate_manifest_v1(manifest):
    """把冻结的 v1 run manifest 显式迁移为可执行的 v2。"""
    if not isinstance(manifest, dict):
        raise StageDagError("v1 manifest 顶层必须是对象")
    if manifest.get("schema_version") != RUN_SCHEMA_V1:
        raise StageDagError("migrate 只接受 pb-stage-run-v1")

    migrated = copy.deepcopy(manifest)
    stages = migrated.get("stages")
    if not isinstance(stages, list):
        raise StageDagError("v1 stages 必须是数组")

    shape_errors = _validate_v1_manifest_shape(manifest)
    if shape_errors:
        raise StageDagError(shape_errors)

    by_id = {stage.get("stage_id"): stage for stage in stages}
    unknown_stage_ids = sorted(set(by_id) - _V1_CREATION_STAGE_IDS)
    if unknown_stage_ids:
        raise StageDagError(
            f"v1 包含未知 stage：{', '.join(unknown_stage_ids)}")
    missing_stage_ids = sorted(_REQUIRED_V1_STAGE_IDS - set(by_id))
    if missing_stage_ids:
        raise StageDagError(
            f"v1 缺少必需 stage：{', '.join(missing_stage_ids)}")

    gate = by_id.get("confirmation_gate")
    if gate and gate.get("status") == "done" and not gate.get("outcome"):
        raise StageDagError("确认门已 done 但缺少 outcome，无法安全迁移")

    revision = by_id.get("revision_loop")
    persistence = by_id.get("persistence")
    if revision and persistence:
        revision_started = revision.get("status") not in {"pending", "cancelled"}
        persistence_started = (
            persistence.get("status") not in {"pending", "cancelled"}
        )
        if revision_started or persistence_started:
            raise StageDagError("v1 revision_loop/persistence 状态存在歧义")

    migrated["schema_version"] = RUN_SCHEMA
    migrated["root_run_id"] = manifest["run_id"]
    migrated["revision_of_run_id"] = None
    migrated["iteration"] = 1
    migrated["status"] = "pending"
    migrated["outcome"] = None
    migrated["revision_feedback"] = []
    migrated["source_artifact_ref"] = None
    migrated["stages"] = [
        stage for stage in stages
        if stage.get("stage_id") != "revision_loop"
    ]
    for stage in migrated["stages"]:
        stage.setdefault("outcome", None)
        stage.setdefault("when", None)
        stage.setdefault("skip_reason", None)
        stage.setdefault("blocked_reason", None)
    persistence = next(
        stage for stage in migrated["stages"]
        if stage["stage_id"] == "persistence"
    )
    persistence["when"] = {
        "stage_id": "confirmation_gate",
        "outcome": "approved",
    }
    return validate_manifest(migrated)


def topological_order(manifest):
    """返回全局拓扑序；发现循环时抛出 StageDagError。"""
    validated = validate_manifest(manifest)
    return _topological_order(validated["stages"])


def ready_stages(manifest):
    """返回所有依赖均已完成的待执行阶段。"""
    return sorted(
        item.stage_id
        for item in resolve_stage_decisions(manifest)
        if item.decision == "ready"
    )


def resolve_stage_decisions(manifest):
    """按依赖状态和条件返回 pending/ready 阶段的调度决策。"""
    validated = validate_manifest(manifest)
    stages = {
        stage["stage_id"]: stage
        for stage in validated["stages"]
    }
    decisions = []
    for stage in validated["stages"]:
        if stage["status"] not in {"pending", "ready"}:
            continue

        dependencies = [stages[dep] for dep in stage["depends_on"]]
        if any(
            dep["status"] in {"failed", "blocked", "cancelled"}
            for dep in dependencies
        ):
            decisions.append(StageDecision(
                stage["stage_id"],
                "block",
                "dependency failed, blocked, or cancelled",
            ))
            continue
        if any(
            dep["status"] in {"pending", "ready", "running"}
            for dep in dependencies
        ):
            decisions.append(StageDecision(
                stage["stage_id"],
                "waiting",
                "dependency is not done",
            ))
            continue

        when = stage.get("when")
        if when is not None:
            condition = stages[when["stage_id"]]
            if condition["outcome"] != when["outcome"]:
                decisions.append(StageDecision(
                    stage["stage_id"],
                    "skip",
                    "condition outcome does not match",
                ))
            else:
                decisions.append(StageDecision(
                    stage["stage_id"],
                    "ready",
                    "condition outcome matched",
                ))
            continue

        if all(dep["status"] == "done" for dep in dependencies):
            decisions.append(StageDecision(
                stage["stage_id"],
                "ready",
                "dependencies are done",
            ))
        elif any(dep["status"] == "skipped" for dep in dependencies):
            decisions.append(StageDecision(
                stage["stage_id"],
                "skip",
                "dependency was skipped",
            ))
        else:
            decisions.append(StageDecision(
                stage["stage_id"],
                "waiting",
                "dependencies are not ready",
            ))
    return decisions


def next_batches(manifest):
    """返回下一批可执行阶段，并正确处理条件阶段。"""
    validated = validate_manifest(manifest)
    ready = [
        item.stage_id
        for item in resolve_stage_decisions(validated)
        if item.decision == "ready"
    ]
    return [sorted(ready)] if ready else []


def finalize_run(manifest, outcome):
    """按确认结果收敛 run 及其下游阶段。"""
    validated = validate_manifest(manifest)
    if outcome not in CONFIRMATION_OUTCOMES:
        raise StageDagError(f"非法 run outcome：{outcome!r}")

    if outcome == "approved":
        persistence = next(
            stage for stage in validated["stages"]
            if stage["stage_id"] == "persistence"
        )
        reminder = next(
            stage for stage in validated["stages"]
            if stage["stage_id"] == "knowledge_reminder"
        )
        if persistence["status"] != "done":
            raise StageDagError("persistence 未完成，run 不能标记 approved")
        if reminder["status"] != "done":
            raise StageDagError(
                "knowledge_reminder 未完成，run 不能标记 approved")
        validated["status"] = "completed"
        validated["outcome"] = "approved"
    elif outcome == "revision_requested":
        for stage_id in ("persistence", "knowledge_reminder"):
            stage = next(
                item for item in validated["stages"]
                if item["stage_id"] == stage_id
            )
            if stage["status"] in {"pending", "ready"}:
                stage["status"] = "skipped"
                stage["skip_reason"] = "confirmation_requested_revision"
            elif stage["status"] != "skipped":
                raise StageDagError(
                    f"{stage_id} 状态无法安全收敛为 skipped"
                )
        validated["status"] = "completed"
        validated["outcome"] = "revision_requested"
    else:
        validated["status"] = "cancelled"
        validated["outcome"] = "cancelled"
    return validate_manifest(validated)


def parallel_batches(manifest):
    """把 DAG 展开成按波次执行的并行批次。"""
    validated = validate_manifest(manifest)
    if any(stage.get("when") is not None for stage in validated["stages"]):
        raise StageDagError(
            "parallel_batches 不支持条件阶段；请使用 next_batches"
        )
    work = {
        stage["stage_id"]: dict(stage)
        for stage in validated["stages"]
    }
    batches = []
    while True:
        if any(
            stage["status"] in ("blocked", "failed", "cancelled")
            for stage in work.values()
        ):
            break
        ready = sorted(
            sid for sid, stage in work.items()
            if stage["status"] in ("pending", "ready")
            and all(work[dep]["status"] == "done" for dep in stage["depends_on"])
        )
        if not ready:
            break
        batches.append(ready)
        for sid in ready:
            work[sid]["status"] = "done"
    return batches


def transition_stage(manifest, stage_id, status, **fields):
    """校验并执行一次阶段状态迁移。"""
    if status not in STAGE_STATUSES:
        raise StageDagError(f"非法阶段状态：{status!r}")
    validated = validate_manifest(manifest)
    index = next(
        (i for i, stage in enumerate(validated["stages"])
         if stage["stage_id"] == stage_id),
        None,
    )
    if index is None:
        raise StageDagError(f"未知阶段：{stage_id}")

    current = validated["stages"][index]["status"]
    if status not in TRANSITIONS[current]:
        raise StageDagError(
            f"阶段 {stage_id} 不允许从 {current} 迁移到 {status}")
    validated["stages"][index]["status"] = status
    validated["stages"][index].update(fields)
    current_stage = validated["stages"][index]

    if status == "skipped" and not fields.get("skip_reason"):
        raise StageDagError("skipped 阶段必须提供 skip_reason")

    if status == "blocked" and not fields.get("blocked_reason"):
        raise StageDagError("blocked 阶段必须提供 blocked_reason")

    if status == "done" and current_stage["gate"] == "confirmation":
        outcome = fields.get("outcome")
        if outcome not in CONFIRMATION_OUTCOMES:
            raise StageDagError(f"confirmation outcome 非法：{outcome!r}")

    if status == "done" and current_stage["gate"] != "confirmation":
        if fields.get("outcome") is not None:
            raise StageDagError("非 confirmation 阶段不得写入 outcome")
    return validated


def validate_task(task, manifest=None):
    """校验任务信封；传入 manifest 时同时校验阶段与执行者归属。"""
    if not isinstance(task, dict):
        raise StageDagError("task 顶层必须是对象")
    errors = []
    if task.get("schema_version") != TASK_SCHEMA:
        errors.append(f"task schema_version 必须是 {TASK_SCHEMA}")
    for key in ("run_id", "task_id", "stage_id", "agent_id",
                "logical_target", "resume_context", "return_channel"):
        _require_nonempty_str(task.get(key), key, errors)
    for key in ("inputs", "constraints"):
        if not isinstance(task.get(key), dict):
            errors.append(f"{key} 必须是对象")
    if not isinstance(task.get("output_paths"), list):
        errors.append("output_paths 必须是数组")
    elif any(not isinstance(item, str) for item in task["output_paths"]):
        errors.append("output_paths 的每一项必须是字符串")
    elif isinstance(task.get("agent_id"), str) and task["agent_id"].strip():
        expected_prefix = f"outputs/{task['agent_id']}/"
        for index, path in enumerate(task["output_paths"]):
            normalized = path.replace("\\", "/")
            if ".." in normalized.split("/"):
                errors.append(
                    f"output_paths[{index}] 禁止路径穿越：{path!r}")
            elif not normalized.startswith(expected_prefix):
                errors.append(
                    f"output_paths[{index}] 必须以 {expected_prefix!r} 开头，"
                    f"实际值：{path!r}")
    if errors:
        raise StageDagError(errors)

    if manifest is None:
        return copy.deepcopy(task)

    validated_manifest = validate_manifest(manifest)
    if task["run_id"] != validated_manifest.get("run_id"):
        raise StageDagError("task.run_id 与 manifest.run_id 不一致")
    stage = next(
        (stage for stage in validated_manifest["stages"]
         if stage["stage_id"] == task["stage_id"]),
        None,
    )
    if stage is None:
        raise StageDagError(f"task 引用未知阶段：{task['stage_id']}")
    if task["agent_id"] != stage["assignee"]:
        raise StageDagError(
            f"task.agent_id 与阶段负责人不一致：{task['agent_id']} != {stage['assignee']}")
    return copy.deepcopy(task)


def validate_result(result):
    """校验结果信封与协作请求。"""
    if not isinstance(result, dict):
        raise StageDagError("result 顶层必须是对象")
    errors = []
    if result.get("schema_version") != RESULT_SCHEMA:
        errors.append(f"result schema_version 必须是 {RESULT_SCHEMA}")
    for key in ("run_id", "task_id", "stage_id", "agent_id", "resume_context"):
        _require_nonempty_str(result.get(key), key, errors)
    status = result.get("status")
    if status not in RESULT_STATUSES:
        errors.append(f"result.status 非法：{status!r}")
    if not isinstance(result.get("result"), dict):
        errors.append("result.result 必须是对象")
    for key in ("artifact_refs", "requests", "risks"):
        if not isinstance(result.get(key), list):
            errors.append(f"result.{key} 必须是数组")

    requests = result.get("requests")
    if isinstance(requests, list):
        for index, request in enumerate(requests):
            if not isinstance(request, dict):
                errors.append(f"requests[{index}] 必须是对象")
                continue
            if request.get("type") not in REQUEST_TYPES:
                errors.append(
                    f"requests[{index}].type 非法：{request.get('type')!r}")
            _require_nonempty_str(
                request.get("logical_target"),
                f"requests[{index}].logical_target", errors)
            if not isinstance(request.get("payload"), dict):
                errors.append(f"requests[{index}].payload 必须是对象")
            if not isinstance(request.get("blocking"), bool):
                errors.append(f"requests[{index}].blocking 必须是布尔值")
    if errors:
        raise StageDagError(errors)
    return copy.deepcopy(result)


def _v2_stage(stage_id, assignee, depends_on, gate, when=None):
    return {
        "stage_id": stage_id,
        "assignee": assignee,
        "depends_on": depends_on,
        "status": "pending",
        "gate": gate,
        "outcome": None,
        "when": when,
        "input_refs": [],
        "output_refs": [],
        "skip_reason": None,
        "blocked_reason": None,
    }


def _template_manifest():
    return {
        "schema_version": RUN_SCHEMA,
        "run_id": "00000000-example-0001",
        "root_run_id": "00000000-example-0001",
        "revision_of_run_id": None,
        "iteration": 1,
        "intent": "creation",
        "artifact_type": "script",
        "mode": "full",
        "project_root": "E:/workspace/example-project",
        "status": "pending",
        "outcome": None,
        "revision_feedback": [],
        "source_artifact_ref": None,
        "stages": [
            _v2_stage(
                "session_init", "pb-intake-agent", [], "none"),
            _v2_stage(
                "brief_gate", "pb-intake-agent", ["session_init"], "brief"),
        ],
    }


def _creation_template_manifest():
    """spec 第 5 节定义的 11 阶段 creation 全流程模板。"""
    lead = "picturebook-screenwriter-team-lead"
    return {
        "schema_version": RUN_SCHEMA,
        "run_id": "00000000-creation-0001",
        "root_run_id": "00000000-creation-0001",
        "revision_of_run_id": None,
        "iteration": 1,
        "intent": "creation",
        "artifact_type": "script",
        "mode": "full",
        "project_root": "E:/workspace/example-project",
        "status": "pending",
        "outcome": None,
        "revision_feedback": [],
        "source_artifact_ref": None,
        "stages": [
            _v2_stage(
                "session_init", "pb-intake-agent", [], "none"),
            _v2_stage(
                "brief_gate", "pb-intake-agent", ["session_init"], "brief"),
            _v2_stage(
                "knowledge_load", "pb-knowledge-steward", ["session_init"], "authority"),
            _v2_stage(
                "creation_delegate", "pb-screenwriter",
                ["brief_gate", "knowledge_load"], "none"),
            _v2_stage(
                "preflight", "pb-preflight-agent", ["creation_delegate"], "redline"),
            _v2_stage(
                "collision_check", "pb-knowledge-steward",
                ["creation_delegate"], "collision"),
            _v2_stage(
                "qa", "pb-quality-reviewer",
                ["preflight", "collision_check"], "quality"),
            _v2_stage("qa_synthesis", lead, ["qa"], "none"),
            _v2_stage(
                "confirmation_gate", lead, ["qa_synthesis"], "confirmation"),
            _v2_stage(
                "persistence", "pb-persistence-agent",
                ["confirmation_gate"], "none",
                when={
                    "stage_id": "confirmation_gate",
                    "outcome": "approved",
                }),
            _v2_stage(
                "knowledge_reminder", lead, ["persistence"], "none"),
        ],
    }


def _revision_template_manifest():
    """spec 第 12 节定义的 revision 全流程模板。"""
    lead = "picturebook-screenwriter-team-lead"
    return {
        "schema_version": RUN_SCHEMA,
        "run_id": "00000000-revision-0001",
        "root_run_id": "00000000-creation-0001",
        "revision_of_run_id": "00000000-creation-0001",
        "iteration": 2,
        "intent": "revision",
        "artifact_type": "script",
        "mode": "full",
        "project_root": "E:/workspace/example-project",
        "status": "pending",
        "outcome": None,
        "revision_feedback": [],
        "source_artifact_ref": None,
        "stages": [
            _v2_stage(
                "revision_init", "pb-intake-agent", [], "none"),
            _v2_stage(
                "knowledge_load", "pb-knowledge-steward",
                ["revision_init"], "authority"),
            _v2_stage(
                "revision_delegate", "pb-screenwriter",
                ["revision_init", "knowledge_load"], "none"),
            _v2_stage(
                "preflight", "pb-preflight-agent",
                ["revision_delegate"], "redline"),
            _v2_stage(
                "collision_check", "pb-knowledge-steward",
                ["revision_delegate"], "collision"),
            _v2_stage(
                "qa", "pb-quality-reviewer",
                ["preflight", "collision_check"], "quality"),
            _v2_stage("qa_synthesis", lead, ["qa"], "none"),
            _v2_stage(
                "confirmation_gate", lead, ["qa_synthesis"], "confirmation"),
            _v2_stage(
                "persistence", "pb-persistence-agent",
                ["confirmation_gate"], "none",
                when={
                    "stage_id": "confirmation_gate",
                    "outcome": "approved",
                }),
            _v2_stage(
                "knowledge_reminder", lead, ["persistence"], "none"),
        ],
    }


def build_revision_manifest(parent_run, feedback, artifact_ref, run_id):
    """从已请求修订的父 run 创建下一轮 revision run。"""
    parent = validate_manifest(parent_run)
    if parent["status"] != "completed":
        raise StageDagError("父 run 必须 completed")
    if parent["outcome"] != "revision_requested":
        raise StageDagError("父 run outcome 必须为 revision_requested")
    if not isinstance(feedback, list) or not feedback:
        raise StageDagError("revision_feedback 必须非空")
    errors = []
    _require_nonempty_str(artifact_ref, "artifact_ref", errors)
    if errors:
        raise StageDagError(errors)

    manifest = _revision_template_manifest()
    manifest["run_id"] = run_id
    manifest["root_run_id"] = parent["root_run_id"]
    manifest["revision_of_run_id"] = parent["run_id"]
    manifest["iteration"] = parent["iteration"] + 1
    manifest["artifact_type"] = parent["artifact_type"]
    manifest["project_root"] = parent["project_root"]
    manifest["revision_feedback"] = copy.deepcopy(feedback)
    manifest["source_artifact_ref"] = artifact_ref
    return validate_manifest(manifest)


def _illustration_template_manifest():
    """spec 第 5 节定义的 8 阶段插画子图模板。"""
    art = "picturebook-art-agent"
    lead = "picturebook-screenwriter-team-lead"
    return {
        "schema_version": RUN_SCHEMA,
        "run_id": "00000000-illustration-0001",
        "root_run_id": "00000000-illustration-0001",
        "revision_of_run_id": None,
        "iteration": 1,
        "intent": "creation",
        "artifact_type": "illustration",
        "mode": "full",
        "project_root": "E:/workspace/example-project",
        "status": "pending",
        "outcome": None,
        "revision_feedback": [],
        "source_artifact_ref": None,
        "stages": [
            _v2_stage(
                "illustration_init", "pb-intake-agent", [], "script-approved"),
            _v2_stage(
                "asset_extraction", art, ["illustration_init"], "none"),
            _v2_stage(
                "asset_confirmation", lead, ["asset_extraction"], "asset-list"),
            _v2_stage(
                "asset_preproduction", art, ["asset_confirmation"], "none"),
            _v2_stage(
                "asset_final_confirmation", lead,
                ["asset_preproduction"], "asset-final"),
            _v2_stage(
                "illustration_generation", art,
                ["asset_final_confirmation"], "none"),
            _v2_stage(
                "html_export", art, ["illustration_generation"], "none"),
            _v2_stage(
                "illustration_delivery", lead, ["html_export"], "none"),
        ],
    }


def _creation_template_manifest_v1():
    """Return the frozen v1 creation template used only by migration tests."""
    def _stage(sid, assignee, deps, gate):
        return {
            "stage_id": sid,
            "assignee": assignee,
            "depends_on": deps,
            "status": "pending",
            "gate": gate,
            "input_refs": [],
            "output_refs": [],
        }

    lead = "picturebook-screenwriter-team-lead"
    return {
        "schema_version": RUN_SCHEMA_V1,
        "run_id": "00000000-creation-0001",
        "intent": "creation",
        "artifact_type": "script",
        "mode": "full",
        "project_root": "E:/workspace/example-project",
        "stages": [
            _stage("session_init", "pb-intake-agent", [], "none"),
            _stage("brief_gate", "pb-intake-agent", ["session_init"], "brief"),
            _stage("knowledge_load", "pb-knowledge-steward", ["session_init"], "authority"),
            _stage("creation_delegate", "pb-screenwriter", ["brief_gate", "knowledge_load"], "none"),
            _stage("preflight", "pb-preflight-agent", ["creation_delegate"], "redline"),
            _stage("collision_check", "pb-knowledge-steward", ["creation_delegate"], "collision"),
            _stage("qa", "pb-quality-reviewer", ["preflight", "collision_check"], "quality"),
            _stage("qa_synthesis", lead, ["qa"], "none"),
            _stage("confirmation_gate", lead, ["qa_synthesis"], "confirmation"),
            _stage("revision_loop", "pb-screenwriter", ["confirmation_gate"], "revision"),
            _stage("persistence", "pb-persistence-agent", ["confirmation_gate"], "none"),
            _stage("knowledge_reminder", lead, ["persistence"], "none"),
        ],
    }


def run_cli(argv=None):
    """供 hook / 主编调用的命令行入口。"""
    parser = ArgumentParser(description="阶段 DAG 调度器")
    parser.add_argument("--manifest", help="run manifest JSON 路径")
    parser.add_argument(
        "--action",
        required=True,
        choices=(
            "validate", "ready", "batches", "template", "migrate",
            "creation-template", "revision-template",
            "illustration-template",
        ),
    )
    args = parser.parse_args(argv)

    try:
        if args.action == "template":
            print(json.dumps(_template_manifest(), ensure_ascii=False, indent=2))
            return 0
        if args.action == "creation-template":
            print(json.dumps(_creation_template_manifest(), ensure_ascii=False, indent=2))
            return 0
        if args.action == "revision-template":
            print(json.dumps(
                _revision_template_manifest(), ensure_ascii=False, indent=2))
            return 0
        if args.action == "illustration-template":
            print(json.dumps(
                _illustration_template_manifest(), ensure_ascii=False, indent=2))
            return 0
        if not args.manifest:
            parser.error("--manifest 在 validate/ready/batches/migrate 模式下必填")
        with open(args.manifest, encoding="utf-8") as fh:
            manifest = json.load(fh)
        if args.action == "migrate":
            print(json.dumps(
                migrate_manifest_v1(manifest),
                ensure_ascii=False,
                indent=2,
            ))
            return 0
        if args.action == "validate":
            validate_manifest(manifest)
            print("OK")
        elif args.action == "ready":
            print(json.dumps(ready_stages(manifest), ensure_ascii=False, indent=2))
        elif args.action == "batches":
            print(json.dumps(parallel_batches(manifest), ensure_ascii=False, indent=2))
        return 0
    except StageDagError as exc:
        print("[stage_dag] 清单或调度失败：", file=sys.stderr)
        for error in exc.errors:
            print(f"- {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(run_cli())
