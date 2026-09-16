"""Versioned durable state for one v2 Stage lifecycle."""

from __future__ import annotations

import hashlib
import os
import re
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .artifact_chain import validate_artifact_chain
from .json_contract import dumps_strict_json, loads_strict_json
from .models import StoryLoopError, fields, identifier, require
from .stage_agents import adapter_control_root
from .stage_config import validate_loop_config
from .stage_result import (manual_ack_from_result, safe_manual_ack, safe_result,
                           validate_green_result, validate_result_against_config)
from .storage import locked_file, write_json


PROTOCOL = "loop.state.v2"
MAX_STATE_BYTES = 1024 * 1024
STATE_FIELDS = {
    "protocol", "project_id", "config_digest", "stage", "payload_digest", "status",
    "attempts_started", "consumed_ms", "reserved_ms", "last_failure_code",
    "last_failure_fingerprint", "repeated_failure_count", "next_eligible_at_ms",
    "artifact_chain",
    "last_wall_time_ms", "attempt_workspace_digest", "pending_manual_ack", "result"}
STATUSES = {"READY", "RUNNING", "BACKOFF", "NEEDS_INPUT",
            "ARCHITECTURE_DECISION_REQUIRED", "UNSAFE_CONTAINMENT",
            "BUDGET_EXHAUSTED", "REPEATED_FAILURE", "WORKSPACE_TAINTED",
            "STAGE_GREEN", "FAILED"}
DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}")
NONTERMINAL_STATUSES = {"NEEDS_INPUT", "ARCHITECTURE_DECISION_REQUIRED",
                        "UNSAFE_CONTAINMENT"}
RECOVERABLE_STATUSES = NONTERMINAL_STATUSES | {"WORKSPACE_TAINTED"}
COUNTER_FIELDS = {"attempts_started", "consumed_ms", "reserved_ms",
                  "repeated_failure_count", "next_eligible_at_ms", "last_wall_time_ms"}

def stage_initialization_path(project: Path, stage_id: str) -> Path:
    """Return the independent marker that prevents deleted-state budget resets."""
    identifier(stage_id, "stage id")
    return adapter_control_root(project) / "state" / f"{stage_id}.initialized.json"

def stage_was_initialized(project: Path, stage_id: str) -> bool:
    """Check marker existence without following a broken or hostile link."""
    try:
        stage_initialization_path(project, stage_id).lstat()
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise StoryLoopError("Cannot inspect Stage initialization marker") from exc

def mark_stage_initialized(project: Path, stage_id: str) -> None:
    """Persist the independent Stage initialization marker atomically."""
    if not stage_was_initialized(project, stage_id):
        write_json(stage_initialization_path(project, stage_id), {
            "protocol": "loop.state.initialized.v2", "stage": stage_id,
        })

def stage_state_path(project: Path, stage_id: str) -> Path:
    """Return the external control-plane path for one Stage state."""
    identifier(stage_id, "stage id")
    return adapter_control_root(project) / "state" / f"{stage_id}.json"


@contextmanager
def locked_stage_state(project: Path, stage_id: str) -> Iterator[Path]:
    """Serialize lifecycle mutations using a crash-released external lock."""
    path = stage_state_path(project, stage_id).with_suffix(".lock")
    with locked_file(path) as acquired:
        yield acquired

def new_stage_state(project: Path, config: dict, stage_id: str,
                    payload: dict) -> dict:
    """Create an unpersisted state bound to non-sensitive input digests."""
    identity = _identity(project, config, stage_id, payload)
    return {
        "protocol": PROTOCOL,
        **identity,
        "status": "READY",
        "attempts_started": 0,
        "consumed_ms": 0,
        "reserved_ms": 0,
        "last_failure_code": None,
        "last_failure_fingerprint": None,
        "repeated_failure_count": 0,
        "next_eligible_at_ms": 0,
        "last_wall_time_ms": 0,
        "attempt_workspace_digest": None,
        "artifact_chain": [],
        "pending_manual_ack": None,
        "result": None,
    }

def save_stage_state(project: Path, state: dict) -> None:
    """Validate and atomically replace one external Stage state document."""
    _validate_state(state)
    write_json(
        stage_state_path(project, state["stage"]), state, max_bytes=MAX_STATE_BYTES,
    )

def load_stage_state(project: Path, config: dict, stage_id: str,
                     payload: dict) -> dict:
    """Load a strict state and reject stale identity instead of resetting it."""
    state = _read_stage_state(stage_state_path(project, stage_id))
    require(_identity(project, config, stage_id, payload).items() <= state.items(),
            "Stage state identity does not match current inputs")
    validate_result_against_config(state, config, stage_id)
    return state

def load_stage_state_for_resume(project: Path, config: dict, stage_id: str,
                                payload: dict) -> dict:
    """Allow an explicit waiting-state recovery to bind only its new payload."""
    state = _read_stage_state(stage_state_path(project, stage_id))
    identity = _identity(project, config, stage_id, payload)
    if identity.items() <= state.items():
        validate_result_against_config(state, config, stage_id)
        return state
    stable_names = {"project_id", "config_digest", "stage"}
    require(state["status"] in NONTERMINAL_STATUSES and all(
        state[name] == identity[name] for name in stable_names
    ), "Stage state identity does not match a recoverable input")
    return {**state, "payload_digest": identity["payload_digest"]}

def _read_stage_state(path: Path) -> dict:
    try:
        before = path.lstat()
        _validate_state_file(before)
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as handle:
            current = os.fstat(handle.fileno())
            _validate_state_file(current)
            require((before.st_dev, before.st_ino) == (current.st_dev, current.st_ino),
                    "Stage state file changed unexpectedly")
            raw = handle.read(MAX_STATE_BYTES + 1)
        require(len(raw) <= MAX_STATE_BYTES, "Stage state exceeds size limit")
        encoded = raw.decode("utf-8-sig")
    except StoryLoopError:
        raise
    except (OSError, UnicodeError) as exc:
        raise StoryLoopError(f"Cannot read Stage state: {path.name}") from exc
    state = loads_strict_json(encoded, "Stage state", MAX_STATE_BYTES)
    _validate_state(state)
    return state

def _validate_state_file(status: os.stat_result) -> None:
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    require(stat.S_ISREG(status.st_mode) and status.st_nlink == 1,
            "Stage state must be a single-link regular file")
    require(not getattr(status, "st_file_attributes", 0) & reparse,
            "Stage state cannot be a reparse point")
    require(status.st_size <= MAX_STATE_BYTES, "Stage state exceeds size limit")

def begin_attempt(state: dict, config: dict, now_ms: int, reserve_ms: int,
                  workspace_digest: str) -> dict:
    """Return a RUNNING state with attempt and crash-safe time precharged."""
    validate_loop_config(config)
    _validate_state(state)
    _millis(now_ms, "current time")
    _millis(reserve_ms, "reserved time", positive=True)
    require(isinstance(workspace_digest, str) and DIGEST_PATTERN.fullmatch(workspace_digest),
            "Invalid attempt workspace digest")
    require(state["status"] in {"READY", "BACKOFF"},
            "Stage state cannot start an attempt")
    require(now_ms >= state["last_wall_time_ms"], "Wall clock moved backwards")
    require(now_ms >= state["next_eligible_at_ms"], "Stage attempt is in backoff")
    budgets = config["budgets"]
    require(state["attempts_started"] < budgets["attempts_per_stage"],
            "Stage attempt budget exhausted")
    remaining = budgets["total_seconds"] * 1000 - state["consumed_ms"]
    require(remaining > 0 and reserve_ms <= remaining, "Stage time budget exhausted")
    return {
        **state,
        "status": "RUNNING",
        "attempts_started": state["attempts_started"] + 1,
        "reserved_ms": reserve_ms,
        "next_eligible_at_ms": 0,
        "last_wall_time_ms": now_ms,
        "attempt_workspace_digest": workspace_digest,
        "result": None,
    }

def settle_attempt_failure(state: dict, config: dict, error: StoryLoopError,
                           elapsed_ms: int, now_ms: int) -> dict:
    """Reconcile actual time and classify one machine-identifiable failure."""
    validate_loop_config(config)
    _validate_state(state)
    _millis(elapsed_ms, "elapsed time")
    _millis(now_ms, "current time")
    require(state["status"] == "RUNNING" and state["reserved_ms"] > 0,
            "Stage state has no running attempt")
    require(isinstance(error, StoryLoopError), "Invalid Stage failure")
    require(now_ms >= state["last_wall_time_ms"], "Wall clock moved backwards")
    settled = {
        **state,
        "consumed_ms": state["consumed_ms"] + elapsed_ms,
        "reserved_ms": 0,
        "last_wall_time_ms": now_ms,
        "attempt_workspace_digest": None,
    }
    return _failure_transition(
        settled, config, error.code, _failure_fingerprint(error), now_ms,
    )

def settle_attempt_tainted(state: dict, config: dict, error: StoryLoopError,
                           elapsed_ms: int, now_ms: int) -> dict:
    """Stop automatic retries after a verified workspace boundary violation."""
    validate_loop_config(config)
    _validate_state(state)
    _millis(elapsed_ms, "elapsed time")
    _millis(now_ms, "current time")
    require(state["status"] == "RUNNING" and error.code == "WORKSPACE_TAINTED",
            "Invalid tainted Stage result")
    require(now_ms >= state["last_wall_time_ms"], "Wall clock moved backwards")
    return {
        **state,
        "status": "WORKSPACE_TAINTED",
        "consumed_ms": state["consumed_ms"] + elapsed_ms,
        "reserved_ms": 0,
        "last_failure_code": None,
        "last_failure_fingerprint": None,
        "repeated_failure_count": 0,
        "next_eligible_at_ms": 0,
        "last_wall_time_ms": now_ms,
    }

def settle_attempt_nonterminal(state: dict, config: dict, error: StoryLoopError,
                               elapsed_ms: int, now_ms: int) -> dict:
    """Persist a recoverable stop without spending an implementation attempt."""
    validate_loop_config(config)
    _validate_state(state)
    _millis(elapsed_ms, "elapsed time")
    _millis(now_ms, "current time")
    require(state["status"] == "RUNNING" and state["reserved_ms"] > 0,
            "Stage state has no running attempt")
    require(isinstance(error, StoryLoopError) and error.code in NONTERMINAL_STATUSES,
            "Invalid nonterminal Stage result")
    require(elapsed_ms <= state["reserved_ms"], "Stage exceeded its reserved time")
    require(now_ms >= state["last_wall_time_ms"], "Wall clock moved backwards")
    total = state["consumed_ms"] + elapsed_ms
    exhausted = total >= config["budgets"]["total_seconds"] * 1000
    return {
        **state,
        "status": "BUDGET_EXHAUSTED" if exhausted else error.code,
        "attempts_started": state["attempts_started"] - 1,
        "consumed_ms": total,
        "reserved_ms": 0,
        "last_failure_code": None,
        "last_failure_fingerprint": None,
        "repeated_failure_count": 0,
        "next_eligible_at_ms": 0,
        "last_wall_time_ms": now_ms,
        "attempt_workspace_digest": (
            state["attempt_workspace_digest"]
            if error.code == "UNSAFE_CONTAINMENT" else None
        ),
    }

def resume_nonterminal_state(state: dict, acknowledge_unsafe: bool = False,
                             workspace_digest: str | None = None) -> dict:
    """Return a recoverable stop to READY after new input or explicit safety ack."""
    _validate_state(state)
    require(type(acknowledge_unsafe) is bool, "Invalid safety acknowledgement")
    require(state["status"] in RECOVERABLE_STATUSES,
            "Stage state is not waiting for recovery")
    unsafe = {"UNSAFE_CONTAINMENT", "WORKSPACE_TAINTED"}
    require(state["status"] not in unsafe or acknowledge_unsafe,
            "Unsafe recovery requires explicit acknowledgement")
    if state["status"] in unsafe:
        require(isinstance(workspace_digest, str) and
                DIGEST_PATTERN.fullmatch(workspace_digest),
                "Unsafe recovery requires a workspace digest")
        require(workspace_digest == state["attempt_workspace_digest"],
                "Restore the workspace before unsafe recovery")
    return {
        **state, "status": "READY", "attempt_workspace_digest": None,
        "last_failure_code": None,
        "last_failure_fingerprint": None, "repeated_failure_count": 0,
    }

def resume_after_replan(state: dict, config: dict) -> dict:
    """Reset only a repeated-failure batch while preserving consumed budgets."""
    validate_loop_config(config)
    _validate_state(state)
    require(state["status"] == "REPEATED_FAILURE",
            "Stage state is not eligible for replan")
    budgets = config["budgets"]
    require(state["attempts_started"] < budgets["attempts_per_stage"]
            and state["consumed_ms"] < budgets["total_seconds"] * 1000,
            "Replan cannot restore an exhausted Stage budget")
    return {
        **state, "status": "READY", "last_failure_code": None,
        "last_failure_fingerprint": None, "repeated_failure_count": 0,
        "next_eligible_at_ms": 0,
    }

def settle_attempt_success(state: dict, config: dict, result: dict,
                           elapsed_ms: int, now_ms: int) -> dict:
    """Reconcile actual time and retain only bounded machine-safe evidence."""
    validate_loop_config(config)
    _validate_state(state)
    _millis(elapsed_ms, "elapsed time")
    _millis(now_ms, "current time")
    require(state["status"] == "RUNNING" and state["reserved_ms"] > 0,
            "Stage state has no running attempt")
    require(elapsed_ms <= state["reserved_ms"], "Stage exceeded its reserved time")
    require(now_ms >= state["last_wall_time_ms"], "Wall clock moved backwards")
    total = state["consumed_ms"] + elapsed_ms
    require(total <= config["budgets"]["total_seconds"] * 1000,
            "Stage time budget exhausted")
    persisted_result = safe_result(result, state["stage"])
    validate_green_result(persisted_result, config, state["stage"])
    return {
        **state,
        "status": "STAGE_GREEN",
        "consumed_ms": total,
        "reserved_ms": 0,
        "last_failure_code": None,
        "last_failure_fingerprint": None,
        "repeated_failure_count": 0,
        "next_eligible_at_ms": 0,
        "last_wall_time_ms": now_ms,
        "attempt_workspace_digest": None,
        "pending_manual_ack": manual_ack_from_result(result, state["stage"]),
        "result": persisted_result,
    }

def _failure_fingerprint(error: StoryLoopError) -> str:
    value = {"code": error.code}
    evidence = error.details.get("evidence")
    if isinstance(evidence, dict):
        names = (
            "stage", "judge", "phase", "status", "outcome", "exit_code",
            "command_digest", "before_digest", "after_digest",
        )
        value["evidence"] = {name: evidence.get(name) for name in names}
    encoded = dumps_strict_json(value, "Failure identity", sort_keys=True)
    return _digest(encoded)

def recover_interrupted_attempt(state: dict, config: dict, now_ms: int,
                                workspace_digest: str) -> dict:
    """Charge a crashed attempt and block retry if its workspace changed."""
    validate_loop_config(config)
    _validate_state(state)
    _millis(now_ms, "current time")
    require(isinstance(workspace_digest, str) and DIGEST_PATTERN.fullmatch(workspace_digest),
            "Invalid recovery workspace digest")
    require(state["status"] == "RUNNING" and state["reserved_ms"] > 0,
            "Stage state has no interrupted attempt")
    require(now_ms >= state["last_wall_time_ms"], "Wall clock moved backwards")
    charged = {
        **state,
        "consumed_ms": state["consumed_ms"] + state["reserved_ms"],
        "reserved_ms": 0,
        "last_wall_time_ms": now_ms,
        "attempt_workspace_digest": None,
    }
    if workspace_digest != state["attempt_workspace_digest"]:
        return {
            **charged, "status": "WORKSPACE_TAINTED",
            "attempt_workspace_digest": state["attempt_workspace_digest"],
            "last_failure_code": None,
            "last_failure_fingerprint": None, "repeated_failure_count": 0,
            "next_eligible_at_ms": 0,
        }
    return _failure_transition(
        charged, config, "INTERRUPTED", _digest("INTERRUPTED"), now_ms,
    )

def _failure_transition(state: dict, config: dict, code: str, fingerprint: str,
                        now_ms: int) -> dict:
    repeated = (
        state["repeated_failure_count"] + 1
        if state["last_failure_fingerprint"] == fingerprint else 1
    )
    budgets = config["budgets"]
    exhausted = (
        state["attempts_started"] >= budgets["attempts_per_stage"]
        or state["consumed_ms"] >= budgets["total_seconds"] * 1000
    )
    if exhausted:
        status = "BUDGET_EXHAUSTED"
    elif repeated >= budgets["repeated_failure_limit"]:
        status = "REPEATED_FAILURE"
    else:
        status = "BACKOFF"
    delays = budgets["backoff_seconds"]
    index = state["attempts_started"] - 1
    delay_ms = delays[index] * 1000 if status == "BACKOFF" and index < len(delays) else 0
    return {
        **state,
        "status": status,
        "last_failure_code": code,
        "last_failure_fingerprint": fingerprint,
        "repeated_failure_count": repeated,
        "next_eligible_at_ms": now_ms + delay_ms if status == "BACKOFF" else 0,
    }

def _millis(value: object, label: str, positive: bool = False) -> int:
    minimum = 1 if positive else 0
    require(type(value) is int and minimum <= value <= 10**15, f"Invalid {label}")
    return value

def _identity(project: Path, config: dict, stage_id: str, payload: dict) -> dict:
    validate_loop_config(config)
    identifier(stage_id, "stage id")
    require(any(item["id"] == stage_id for item in config["stages"]), "Unknown stage")
    require(isinstance(payload, dict), "Stage payload must be a JSON object")
    payload_json = dumps_strict_json(payload, "Stage payload", sort_keys=True)
    config_json = dumps_strict_json(config, "Loop config", sort_keys=True)
    project_text = str(Path(project).resolve()).casefold()
    return {
        "project_id": _digest(project_text),
        "config_digest": _digest(config_json),
        "stage": stage_id,
        "payload_digest": _digest(payload_json),
    }

def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

def _validate_state(state: dict) -> None:
    fields(state, STATE_FIELDS)
    require(state["protocol"] == PROTOCOL, "Unsupported Stage state protocol")
    identifier(state["stage"], "stage id")
    require(isinstance(state["status"], str) and state["status"] in STATUSES,
            "Invalid Stage state status")
    for name in ("project_id", "config_digest", "payload_digest"):
        value = state[name]
        require(isinstance(value, str) and DIGEST_PATTERN.fullmatch(value),
                f"Invalid {name}")
    for name in COUNTER_FIELDS:
        value = state[name]
        require(type(value) is int and 0 <= value <= 10**15, f"Invalid {name}")
    workspace = state["attempt_workspace_digest"]
    require(workspace is None or (
        isinstance(workspace, str) and DIGEST_PATTERN.fullmatch(workspace)
    ), "Invalid attempt workspace digest")
    failure = state["last_failure_fingerprint"]
    require(failure is None or (
        isinstance(failure, str) and DIGEST_PATTERN.fullmatch(failure)
    ), "Invalid failure fingerprint")
    code = state["last_failure_code"]
    require(code is None or (isinstance(code, str) and 0 < len(code) <= 80),
            "Invalid failure code")
    acknowledgement = state["pending_manual_ack"]
    require(acknowledgement is None or state["status"] == "STAGE_GREEN",
            "Manual acknowledgement is only valid for a green Stage")
    if acknowledgement is not None:
        require(safe_manual_ack(acknowledgement, state["stage"]) == acknowledgement,
                "Invalid persisted manual acknowledgement")
    if state["status"] == "STAGE_GREEN":
        require(safe_result(state["result"], state["stage"]) == state["result"],
                "Invalid persisted Stage result")
    else:
        require(state["result"] is None, "Result is only valid for a green Stage")
    validate_artifact_chain(state)
    _validate_state_relationships(state)

def _validate_state_relationships(state: dict) -> None:
    running = state["status"] == "RUNNING"
    workspace_bound = state["status"] in {
        "RUNNING", "UNSAFE_CONTAINMENT", "WORKSPACE_TAINTED",
    }
    require((state["attempt_workspace_digest"] is not None) == workspace_bound,
            "Attempt workspace digest does not match recoverable status")
    require((state["reserved_ms"] > 0) == running,
            "Stage reservation does not match running status")
    require(not running or state["attempts_started"] > 0,
            "Running Stage has no started attempt")
    has_failure = state["last_failure_fingerprint"] is not None
    require((state["last_failure_code"] is not None) == has_failure,
            "Failure code and fingerprint do not match")
    require((state["repeated_failure_count"] > 0) == has_failure,
            "Failure fingerprint and repeat count do not match")
    require(state["status"] not in {"BACKOFF", "REPEATED_FAILURE"} or has_failure,
            "Failure state has no failure identity")
    if state["status"] == "BACKOFF":
        require(state["next_eligible_at_ms"] >= state["last_wall_time_ms"],
                "Backoff eligibility predates the last transition")
    else:
        require(state["next_eligible_at_ms"] == 0,
                "Only a backoff state can have a next eligible time")
