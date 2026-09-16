"""Dependency-ordered coordination of persistent v2 Stage lifecycles."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

from .coordinator_authority import stage_record, verify_coordinator_records
from .coordinator_state import load_or_create, locked_coordinator, save_coordinator
from .deadline import monotonic_ms
from .final_judges import run_final_judges, safe_final_evidence
from .models import require
from .run_artifacts import record_final_run
from .stage_config import stage_order
from .stage_lifecycle import run_stage_lifecycle


LifecycleRunner = Callable[..., dict]


def run_stage_graph(project: Path, config: dict, payloads: dict,
                    target_stage: str | None = None,
                    lifecycle_runner: LifecycleRunner = run_stage_lifecycle,
                    project_clock_ms: Callable[[], int] | None = None,
                    require_governance: bool = True,
                    **lifecycle_options) -> dict:
    """Run the dependency closure serially and persist each bounded handoff."""
    project = Path(project).resolve()
    require(project.is_dir(), "Project must be an existing directory")
    stages = _selected_stages(config, target_stage)
    with locked_coordinator(project):
        authoritative = lifecycle_runner is run_stage_lifecycle
        state = load_or_create(
            project, config, payloads, target_stage, require_governance,
            verify_stage_states=authoritative,
        )
        if state["status"] == "PROJECT_GREEN":
            return _commit_final_artifact(project, config, state)
        for stage in stages:
            stage_id = stage["id"]
            existing = state["stages"].get(stage_id)
            if existing and existing["status"] == "STAGE_GREEN":
                continue
            _require_dependencies_green(state, stage)
            payload = _stage_payload(payloads, stage, state)
            options = {**lifecycle_options,
                       "upstream_handoffs": payload["upstream"]}
            result = lifecycle_runner(
                project, config, stage_id, payload, **options,
            )
            record = stage_record(stage_id, result)
            state = {**state, "status": record["status"],
                     "stages": {**state["stages"], stage_id: record}}
            if authoritative:
                verify_coordinator_records(
                    project, config, payloads, state,
                    [item["id"] for item in stages],
                )
            save_coordinator(project, state)
            if record["status"] != "STAGE_GREEN":
                return state
        return _finish_graph(
            project, config, state, stages, project_clock_ms, lifecycle_options,
        )


def _finish_graph(project: Path, config: dict, state: dict, stages: list[dict],
                  project_clock_ms: Callable[[], int] | None,
                  lifecycle_options: dict) -> dict:
    state = {**state, "status": "STAGE_GRAPH_GREEN"}
    save_coordinator(project, state)
    completed = [stage["id"] for stage in stages]
    handoffs = [state["stages"][item]["handoff"] for item in completed]
    clock = project_clock_ms or monotonic_ms
    started_ms = clock()
    require(type(started_ms) is int and started_ms >= 0,
            "Invalid project budget clock")
    deadline_ms = started_ms + config["budgets"]["total_seconds"] * 1000
    final = safe_final_evidence(
        run_final_judges(
            project, config, completed, handoffs,
            resolver=lifecycle_options.get("resolver", shutil.which),
            environ=lifecycle_options.get("environ"),
            attempt_deadline_ms=deadline_ms, budget_clock_ms=clock,
        ), config, completed,
    )
    state = {**state, "status": "PROJECT_GREEN", "final": final}
    save_coordinator(project, state)
    return _commit_final_artifact(project, config, state)


def _selected_stages(config: dict, target_stage: str | None) -> list[dict]:
    ordered = stage_order(config)
    if target_stage is None:
        return ordered
    require(any(stage["id"] == target_stage for stage in ordered), "Unknown target stage")
    required = {target_stage}
    for stage in reversed(ordered):
        if stage["id"] in required:
            required.update(stage["depends_on"])
    return [stage for stage in ordered if stage["id"] in required]


def _require_dependencies_green(state: dict, stage: dict) -> None:
    require(all(state["stages"].get(item, {}).get("status") == "STAGE_GREEN"
                for item in stage["depends_on"]), "Stage dependency is not green")


def _stage_payload(payloads: dict, stage: dict, state: dict) -> dict:
    value = payloads.get(stage["id"], {})
    require(isinstance(value, dict), "Stage payload must be a JSON object")
    upstream = [state["stages"][item]["handoff"] for item in stage["depends_on"]]
    return {"input": value, "upstream": upstream}

def _commit_final_artifact(project: Path, config: dict, state: dict) -> dict:
    digest = record_final_run(project, config, state)
    if state["final_artifact_digest"] == digest:
        return state
    state = {**state, "final_artifact_digest": digest}
    save_coordinator(project, state)
    return state