"""Cross-check coordinator summaries against durable per-Stage state."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .json_contract import dumps_strict_json
from .models import require
from .run_artifacts import verify_artifact_history
from .stage_config import stage_order
from .stage_state import load_stage_state


def stage_record(stage_id: str, lifecycle: dict) -> dict:
    """Derive the only coordinator record accepted for a Stage lifecycle."""
    status = lifecycle.get("status")
    require(isinstance(status, str), "Lifecycle returned an invalid status")
    if status != "STAGE_GREEN":
        return {"status": status, "handoff": None}
    result = lifecycle.get("result")
    require(isinstance(result, dict), "Green Stage is missing result evidence")
    encoded = dumps_strict_json(result, "Stage result", sort_keys=True)
    judges = [result["red"], *result["l1"], *result["l2"]]
    handoff = {
        "stage": stage_id,
        "result_digest": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        "changes": result["changes"],
        "judges": [{"id": item["judge"], "outcome": item["outcome"]}
                   for item in judges],
    }
    return {"status": status, "handoff": handoff}


def verify_coordinator_records(project: Path, config: dict, payloads: dict,
                               state: dict, selected: list[str]) -> None:
    """Reject summaries not reproduced from identity-bound Stage state."""
    stages = {item["id"]: item for item in stage_order(config)}
    verified: dict[str, dict] = {}
    for stage_id in selected:
        if stage_id not in state["stages"]:
            break
        stage = stages[stage_id]
        raw = payloads.get(stage_id, {})
        require(isinstance(raw, dict), "Stage payload must be a JSON object")
        upstream = [verified[item]["handoff"] for item in stage["depends_on"]]
        payload = {"input": raw, "upstream": upstream}
        authoritative = load_stage_state(project, config, stage_id, payload)
        verify_artifact_history(project, config, authoritative)
        expected = stage_record(stage_id, authoritative)
        require(state["stages"][stage_id] == expected,
                "Coordinator record does not match authoritative Stage state")
        verified[stage_id] = expected
