"""One-attempt orchestration for a single v2 Stage."""

from __future__ import annotations

import shutil
from collections.abc import Callable, Mapping
from pathlib import Path

from .boundaries import capture_snapshot, verify_stage_attempt_changes
from .deadline import Clock, ensure_time_remaining
from .json_contract import dumps_strict_json
from .models import StoryLoopError, identifier, require
from .stage_agents import invoke_selected_adapter
from .stage_config import validate_loop_config
from .stage_judges import run_stage_judge


def run_stage_attempt(
    project: Path,
    config: dict,
    stage_id: str,
    payload: dict,
    requested_adapter: str | None = None,
    environ: Mapping[str, str] | None = None,
    resolver: Callable[[str], str | None] = shutil.which,
    attempt_deadline_ms: int | None = None,
    budget_clock_ms: Clock | None = None,
) -> dict:
    """Run RED/existing-green, optional implementation, then L1 and L2 once."""
    require(isinstance(payload, dict), "Stage payload must be a JSON object")
    dumps_strict_json(payload, "Stage payload")
    stage = _select_stage(config, stage_id)
    project = project.resolve()
    red_judge = next(item for item in stage["judges"] if item["phase"] == "red")
    red = _judge(
        project, config, stage_id, red_judge["id"], {"stage": stage_id},
        resolver, environ, attempt_deadline_ms, budget_clock_ms,
    )
    ensure_time_remaining(attempt_deadline_ms, budget_clock_ms)
    _require_red_outcome(red)
    invoked, agent_response, changes = _implement(
        project, config, stage, payload, red, requested_adapter, resolver, environ,
        attempt_deadline_ms, budget_clock_ms,
    )
    context = {"stage": stage_id, "changes": changes}
    l1 = _run_phase(project, config, stage, "l1", context, resolver, environ,
                    attempt_deadline_ms, budget_clock_ms)
    l2 = _run_phase(project, config, stage, "l2", context, resolver, environ,
                    attempt_deadline_ms, budget_clock_ms)
    ensure_time_remaining(attempt_deadline_ms, budget_clock_ms)
    return {
        "stage": stage_id,
        "outcome": "STAGE_GREEN",
        "agent_invoked": invoked,
        "agent_response": agent_response,
        "changes": changes,
        "red": red,
        "l1": l1,
        "l2": l2,
    }


def _select_stage(config: dict, stage_id: str) -> dict:
    validate_loop_config(config)
    identifier(stage_id, "stage id")
    stage = next((item for item in config["stages"] if item["id"] == stage_id), None)
    require(stage is not None, "Unknown stage")
    return stage


def _judge(
    project: Path,
    config: dict,
    stage_id: str,
    judge_id: str,
    context: dict,
    resolver: Callable[[str], str | None],
    environ: Mapping[str, str] | None,
    attempt_deadline_ms: int | None,
    budget_clock_ms: Clock | None,
) -> dict:
    ensure_time_remaining(attempt_deadline_ms, budget_clock_ms)
    return run_stage_judge(
        project, config, stage_id, judge_id, context,
        resolver=resolver, environ=environ,
        attempt_deadline_ms=attempt_deadline_ms, budget_clock_ms=budget_clock_ms,
    )


def _require_red_outcome(evidence: dict) -> None:
    if evidence["outcome"] not in {"RED", "EXISTING_GREEN"}:
        _raise_judge(evidence)


def _implement(
    project: Path,
    config: dict,
    stage: dict,
    payload: dict,
    red: dict,
    requested: str | None,
    resolver: Callable[[str], str | None],
    environ: Mapping[str, str] | None,
    attempt_deadline_ms: int | None,
    budget_clock_ms: Clock | None,
) -> tuple[bool, dict | None, list[dict]]:
    if red["outcome"] == "EXISTING_GREEN":
        return False, None, []
    before = _capture_attempt_snapshot(project)
    ensure_time_remaining(attempt_deadline_ms, budget_clock_ms)
    try:
        response = invoke_selected_adapter(
            project, config, "implement", stage["id"], payload,
            requested=requested, environ=environ, resolver=resolver,
            attempt_deadline_ms=attempt_deadline_ms, budget_clock_ms=budget_clock_ms,
        )
    except BaseException as exc:
        unsafe = isinstance(exc, StoryLoopError) and exc.code == "UNSAFE_CONTAINMENT"
        if not unsafe:
            _verify_implementation(before, project, stage)
        raise
    changes = _verify_implementation(before, project, stage)
    ensure_time_remaining(attempt_deadline_ms, budget_clock_ms)
    require(response.get("status") == "ok", "Agent did not complete its invocation")
    return True, response, changes


def _capture_attempt_snapshot(project: Path):
    try:
        return capture_snapshot(
            project, include_control=True, include_ignored=True,
        )
    except StoryLoopError as exc:
        if exc.code == "WORKSPACE_TAINTED":
            raise
        raise StoryLoopError(str(exc), code="WORKSPACE_TAINTED") from exc


def _verify_implementation(before, project: Path, stage: dict) -> list[dict]:
    after = _capture_attempt_snapshot(project)
    changes = verify_stage_attempt_changes(before, after, stage)
    return [{"path": item.path, "kind": item.kind} for item in changes]


def _run_phase(
    project: Path,
    config: dict,
    stage: dict,
    phase: str,
    context: dict,
    resolver: Callable[[str], str | None],
    environ: Mapping[str, str] | None,
    attempt_deadline_ms: int | None,
    budget_clock_ms: Clock | None,
) -> list[dict]:
    evidence = []
    for judge in (item for item in stage["judges"] if item["phase"] == phase):
        result = _judge(
            project, config, stage["id"], judge["id"], context, resolver, environ,
            attempt_deadline_ms, budget_clock_ms,
        )
        ensure_time_remaining(attempt_deadline_ms, budget_clock_ms)
        if result["outcome"] != "GREEN":
            _raise_judge(result)
        evidence.append(result)
    return evidence


def _raise_judge(evidence: dict) -> None:
    codes = {"FAIL": "JUDGE_FAILED", "ERROR": "JUDGE_ERROR", "SKIP": "JUDGE_SKIPPED"}
    code = codes.get(evidence.get("outcome"), "JUDGE_BLOCKED")
    raise StoryLoopError(
        "Stage Judge did not pass", code=code, details={"evidence": evidence},
    )