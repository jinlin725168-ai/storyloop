"""Bounded, configuration-bound evidence retained by Stage state."""

from __future__ import annotations

import re
from pathlib import Path

from .models import fields, identifier, require


DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}")
SAFE_RESULT_FIELDS = {
    "stage", "outcome", "agent_invoked", "changes", "red", "l1", "l2",
}
MANUAL_ACK_FIELDS = {"protocol", "stage", "role", "request_id"}
SAFE_EVIDENCE_FIELDS = (
    "request_id", "stage", "judge", "phase", "status", "outcome", "exit_code",
    "contained", "command_digest", "before_digest", "after_digest",
)


def manual_ack_from_result(result: dict, stage_id: str) -> dict | None:
    """Extract and validate an internal manual acknowledgement receipt."""
    response = result.get("agent_response")
    require(response is None or isinstance(response, dict), "Invalid Agent response")
    if not isinstance(response, dict) or "_manual_ack" not in response:
        return None
    return safe_manual_ack(response["_manual_ack"], stage_id)


def safe_manual_ack(acknowledgement: object, stage_id: str) -> dict:
    """Return the bounded persisted form of a manual acknowledgement."""
    fields(acknowledgement, MANUAL_ACK_FIELDS)
    require(acknowledgement["protocol"] == "loop.manual.ack.v2",
            "Unknown manual acknowledgement protocol")
    require(acknowledgement["stage"] == stage_id,
            "Manual acknowledgement Stage does not match")
    require(acknowledgement["role"] == "implement",
            "Invalid manual acknowledgement role")
    request_id = acknowledgement["request_id"]
    require(isinstance(request_id, str) and DIGEST_PATTERN.fullmatch(request_id),
            "Invalid manual acknowledgement request")
    return {name: acknowledgement[name] for name in MANUAL_ACK_FIELDS}


def safe_result(result: dict, stage_id: str) -> dict:
    """Remove unbounded Agent output and validate Judge evidence."""
    fields(result, SAFE_RESULT_FIELDS, {"agent_response"})
    require(result["stage"] == stage_id, "Stage result identity does not match")
    require(result["outcome"] == "STAGE_GREEN", "Stage result is not green")
    require(type(result["agent_invoked"]) is bool, "Invalid agent invocation flag")
    require(isinstance(result["changes"], list) and len(result["changes"]) <= 1000,
            "Invalid Stage changes")
    require(isinstance(result["l1"], list) and len(result["l1"]) <= 100,
            "Invalid L1 evidence")
    require(isinstance(result["l2"], list) and len(result["l2"]) <= 100,
            "Invalid L2 evidence")
    red = _safe_evidence(result["red"], stage_id)
    l1 = [_safe_evidence(item, stage_id) for item in result["l1"]]
    l2 = [_safe_evidence(item, stage_id) for item in result["l2"]]
    require(red["phase"] == "red" and red["outcome"] in {"RED", "EXISTING_GREEN"},
            "Invalid RED evidence")
    require(all(item["phase"] == "l1" and item["outcome"] == "GREEN" for item in l1),
            "Invalid L1 evidence")
    require(all(item["phase"] == "l2" and item["outcome"] == "GREEN" for item in l2),
            "Invalid L2 evidence")
    return {
        "stage": stage_id, "outcome": "STAGE_GREEN",
        "agent_invoked": result["agent_invoked"],
        "changes": [_safe_change(item) for item in result["changes"]],
        "red": red, "l1": l1, "l2": l2,
    }


def validate_result_against_config(state: dict, config: dict, stage_id: str) -> None:
    """Bind persisted green evidence to the current exact Judge set."""
    if state["status"] == "STAGE_GREEN":
        validate_green_result(state["result"], config, stage_id)


def validate_green_result(result: dict, config: dict, stage_id: str) -> None:
    """Reject missing, reordered, or semantically inconsistent Judge evidence."""
    stage = next(item for item in config["stages"] if item["id"] == stage_id)
    expected = {
        phase: [item["id"] for item in stage["judges"] if item["phase"] == phase]
        for phase in ("red", "l1", "l2")
    }
    require(result["red"]["judge"] == expected["red"][0],
            "Persisted RED Judge does not match configuration")
    require([item["judge"] for item in result["l1"]] == expected["l1"],
            "Persisted L1 Judges do not match configuration")
    require([item["judge"] for item in result["l2"]] == expected["l2"],
            "Persisted L2 Judges do not match configuration")
    existing = result["red"]["outcome"] == "EXISTING_GREEN"
    require(not existing or (
        result["agent_invoked"] is False and not result["changes"]
    ), "Existing-green result cannot contain implementation changes")
    require(existing or result["agent_invoked"] is True,
            "RED implementation result must invoke the Agent")


def _safe_change(change: object) -> dict:
    fields(change, {"path", "kind"})
    path = change["path"]
    require(isinstance(path, str) and 0 < len(path) <= 1000,
            "Invalid changed path")
    require("\\" not in path and not Path(path).is_absolute(),
            "Changed path must be project-relative")
    require(all(part not in {"", ".", ".."} for part in path.split("/")),
            "Invalid changed path")
    require(change["kind"] in {"added", "modified", "deleted"},
            "Invalid change kind")
    return {"path": path, "kind": change["kind"]}


def _safe_evidence(evidence: object, stage_id: str) -> dict:
    require(isinstance(evidence, dict), "Invalid Judge evidence")
    require(set(SAFE_EVIDENCE_FIELDS) <= evidence.keys(), "Incomplete Judge evidence")
    for name in ("request_id", "command_digest", "before_digest", "after_digest"):
        value = evidence[name]
        require(isinstance(value, str) and DIGEST_PATTERN.fullmatch(value),
                f"Invalid Judge {name}")
    require(evidence["stage"] == stage_id, "Judge evidence Stage does not match")
    identifier(evidence["judge"], "judge id")
    require(evidence["phase"] in {"red", "l1", "l2"}, "Invalid Judge phase")
    require(evidence["status"] in {"pass", "fail", "error", "skip"},
            "Invalid Judge status")
    expected_exit = {"pass": 0, "fail": 1, "error": 2, "skip": 3}[evidence["status"]]
    require(evidence["exit_code"] == expected_exit, "Inconsistent Judge exit code")
    require(evidence["outcome"] == _judge_outcome(evidence["phase"], evidence["status"]),
            "Inconsistent Judge outcome")
    require(evidence["contained"] is True, "Judge evidence is not contained")
    return {name: evidence[name] for name in SAFE_EVIDENCE_FIELDS}


def _judge_outcome(phase: str, status: str) -> str:
    if status == "error":
        return "ERROR"
    if status == "skip":
        return "SKIP"
    if phase == "red":
        return "RED" if status == "fail" else "EXISTING_GREEN"
    return "GREEN" if status == "pass" else "FAIL"
