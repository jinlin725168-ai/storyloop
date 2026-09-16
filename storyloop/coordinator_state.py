"""Bounded persistent state for a v2 multi-Stage run."""

from __future__ import annotations

import hashlib
import re
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

from .constitution import governance_identity
from .coordinator_authority import verify_coordinator_records
from .final_judges import safe_final_evidence
from .json_contract import dumps_strict_json
from .models import StoryLoopError, fields, identifier, require
from .run_artifacts import record_final_run
from .safe_json_file import read_bounded_object
from .stage_agents import adapter_control_root
from .stage_config import stage_order, validate_loop_config
from .storage import locked_file, write_json


PROTOCOL = "loop.coordinator.v2"
MAX_STATE_BYTES = 1024 * 1024
STATE_FIELDS = {"protocol", "identity", "status", "target_stage", "stages", "final",
                "final_artifact_digest"}
IDENTITY_FIELDS = {"project", "config", "payloads", "governance"}
DIGEST = re.compile(r"[0-9a-f]{64}")
PROJECT_STATUSES = {"READY", "STAGE_GRAPH_GREEN", "PROJECT_GREEN"}
STAGE_STATUSES = {"READY", "RUNNING", "BACKOFF", "NEEDS_INPUT",
                  "ARCHITECTURE_DECISION_REQUIRED", "UNSAFE_CONTAINMENT",
                  "BUDGET_EXHAUSTED", "REPEATED_FAILURE", "WORKSPACE_TAINTED",
                  "STAGE_GREEN", "FAILED"}


def coordinator_path(project: Path) -> Path:
    return adapter_control_root(project) / "coordinator" / "run.json"


@contextmanager
def locked_coordinator(project: Path):
    with locked_file(coordinator_path(project).with_suffix(".lock")):
        yield


def load_or_create(project: Path, config: dict, payloads: dict,
                   target_stage: str | None,
                   require_governance: bool = True,
                   verify_stage_states: bool = True) -> dict:
    require(type(verify_stage_states) is bool, "Invalid authority check flag")
    identity = coordinator_identity(project, config, payloads, require_governance)
    path = coordinator_path(project)
    if not path.exists():
        return {"protocol": PROTOCOL, "identity": identity, "status": "READY",
                "target_stage": target_stage, "stages": {}, "final": [],
                "final_artifact_digest": None}
    state = read_bounded_object(path, MAX_STATE_BYTES, "Coordinator state")
    validate_coordinator_state(state)
    selected = _selected_ids(config, target_stage)
    recorded = list(state["stages"])
    require(recorded == selected[:len(recorded)],
            "Coordinator stages do not match the selected dependency closure")
    _validate_progress(state, selected)
    completed = [stage["id"] for stage in stage_order(config)
                 if state["stages"].get(stage["id"], {}).get("status") == "STAGE_GREEN"]
    if state["status"] == "PROJECT_GREEN":
        require(safe_final_evidence(state["final"], config, completed) == state["final"],
                "Invalid persisted final evidence")
    else:
        require(not state["final"], "Final evidence requires a green project")
    require(state["identity"] == identity and state["target_stage"] == target_stage,
            "Coordinator state identity does not match current inputs")
    if verify_stage_states:
        verify_coordinator_records(project, config, payloads, state, selected)
    if state["final_artifact_digest"] is not None:
        digest = record_final_run(project, config, state)
        require(digest == state["final_artifact_digest"],
                "Run artifact was tampered")
    return state


def save_coordinator(project: Path, state: dict) -> None:
    validate_coordinator_state(state)
    write_json(coordinator_path(project), state, max_bytes=MAX_STATE_BYTES)


def coordinator_identity(project: Path, config: dict, payloads: dict,
                         require_governance: bool = True) -> dict:
    validate_loop_config(config)
    require(isinstance(payloads, dict), "Stage payloads must be a JSON object")
    encoded = dumps_strict_json(payloads, "Stage payloads", sort_keys=True)
    governed = dumps_strict_json(
        governance_identity(project, config, required=require_governance),
        "Governance identity", sort_keys=True,
    )
    return {"project": _digest(str(project.resolve()).casefold()),
            "config": _digest(dumps_strict_json(config, "Loop config", sort_keys=True)),
            "payloads": _digest(encoded), "governance": _digest(governed)}


def validate_coordinator_state(state: object) -> None:
    fields(state, STATE_FIELDS)
    require(state["protocol"] == PROTOCOL, "Unsupported coordinator protocol")
    fields(state["identity"], IDENTITY_FIELDS)
    require(all(isinstance(value, str) and DIGEST.fullmatch(value)
                for value in state["identity"].values()), "Invalid coordinator identity")
    require(state["status"] in PROJECT_STATUSES | STAGE_STATUSES,
            "Invalid coordinator status")
    target = state["target_stage"]
    require(target is None or bool(identifier(target, "target stage")), "Invalid target stage")
    require(isinstance(state["final"], list) and len(state["final"]) <= 100,
            "Invalid final evidence")
    artifact = state["final_artifact_digest"]
    require(artifact is None or (isinstance(artifact, str)
            and DIGEST.fullmatch(artifact)), "Invalid final artifact digest")
    require(state["status"] == "PROJECT_GREEN" or artifact is None,
            "Final artifact digest requires a green project")
    require(isinstance(state["stages"], dict) and len(state["stages"]) <= 100,
            "Invalid coordinator stages")
    for stage_id, record in state["stages"].items():
        identifier(stage_id, "stage id")
        fields(record, {"status", "handoff"})
        require(record["status"] in STAGE_STATUSES, "Invalid coordinated Stage status")
        require((record["handoff"] is not None) ==
                (record["status"] == "STAGE_GREEN"),
                "Stage handoff does not match its status")
        if record["handoff"] is not None:
            require(isinstance(record["handoff"], dict), "Invalid Stage handoff")
            _validate_handoff(record["handoff"], stage_id)


def _selected_ids(config: dict, target_stage: str | None) -> list[str]:
    ordered = stage_order(config)
    if target_stage is None:
        return [stage["id"] for stage in ordered]
    required = {target_stage}
    for stage in reversed(ordered):
        if stage["id"] in required:
            required.update(stage["depends_on"])
    return [stage["id"] for stage in ordered if stage["id"] in required]


def _validate_progress(state: dict, selected: list[str]) -> None:
    records = state["stages"]
    if state["status"] in {"STAGE_GRAPH_GREEN", "PROJECT_GREEN"}:
        require(list(records) == selected and all(
            record["status"] == "STAGE_GREEN" for record in records.values()
        ), "Green coordinator status requires every selected Stage green")
    elif records:
        require(state["status"] == records[list(records)[-1]]["status"],
                "Coordinator status does not match its latest Stage")
    else:
        require(state["status"] == "READY", "Empty coordinator must be ready")


def _validate_handoff(handoff: dict, stage_id: str) -> None:
    fields(handoff, {"stage", "result_digest", "changes", "judges"})
    require(handoff["stage"] == stage_id, "Handoff Stage does not match")
    require(isinstance(handoff["result_digest"], str)
            and DIGEST.fullmatch(handoff["result_digest"]), "Invalid handoff digest")
    require(isinstance(handoff["changes"], list) and len(handoff["changes"]) <= 1000,
            "Invalid handoff changes")
    require(isinstance(handoff["judges"], list) and len(handoff["judges"]) <= 300,
            "Invalid handoff Judges")
    for change in handoff["changes"]:
        fields(change, {"path", "kind"})
        path = change["path"]
        require(isinstance(path, str) and 0 < len(path) <= 1000
                and "\\" not in path and not PurePosixPath(path).is_absolute()
                and all(part not in {"", ".", ".."} for part in path.split("/")),
                "Invalid handoff path")
        require(change["kind"] in {"added", "modified", "deleted"},
                "Invalid handoff change")
    for judge in handoff["judges"]:
        fields(judge, {"id", "outcome"})
        identifier(judge["id"], "handoff judge")
        require(judge["outcome"] in {"RED", "EXISTING_GREEN", "GREEN"},
                "Invalid handoff Judge outcome")

def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
