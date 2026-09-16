"""Crash-safe persistence around a single v2 Stage attempt."""

from __future__ import annotations

import shutil
import time
from collections.abc import Callable, Mapping
from pathlib import Path

from .artifact_chain import bind_artifact
from .boundaries import capture_snapshot, snapshot_digest
from .models import StoryLoopError, require
from .replan import replan_stage
from .run_artifacts import (
    record_stage_attempt, verify_artifact_history, verify_stage_attempt,
)
from .stage_agents import (
    consume_manual_ack, discover_manual_ack, expected_manual_request_id,
)
from .stage_runner import run_stage_attempt
from .stage_config import select_adapter
from .stage_state import (
    NONTERMINAL_STATUSES,
    RECOVERABLE_STATUSES,
    begin_attempt,
    load_stage_state,
    load_stage_state_for_resume,
    locked_stage_state,
    mark_stage_initialized,
    new_stage_state,
    recover_interrupted_attempt,
    resume_nonterminal_state,
    save_stage_state,
    settle_attempt_failure,
    settle_attempt_nonterminal,
    settle_attempt_success,
    settle_attempt_tainted,
    stage_state_path,
    stage_was_initialized,
)


AttemptRunner = Callable[..., dict]
ReplanRunner = Callable[..., dict]
Clock = Callable[[], int]


def run_stage_lifecycle(
    project: Path,
    config: dict,
    stage_id: str,
    payload: dict,
    requested_adapter: str | None = None,
    environ: Mapping[str, str] | None = None,
    resolver: Callable[[str], str | None] = shutil.which,
    attempt_runner: AttemptRunner = run_stage_attempt,
    reserve_ms: int | None = None, resume_waiting: bool = False,
    acknowledge_unsafe: bool = False,
    wall_clock_ms: Clock | None = None,
    monotonic_ms: Clock | None = None,
    sleeper: Callable[[float], None] = time.sleep,
    replan_runner: ReplanRunner = replan_stage,
    upstream_handoffs: list[dict] | None = None,
) -> dict:
    """Retry only the selected Stage, sleeping outside its process lock."""
    wall_clock_ms = wall_clock_ms or _wall_clock_ms
    while True:
        state = run_persisted_attempt(
            project, config, stage_id, payload,
            requested_adapter=requested_adapter, environ=environ,
            resolver=resolver, attempt_runner=attempt_runner,
            reserve_ms=reserve_ms, resume_waiting=resume_waiting,
            acknowledge_unsafe=acknowledge_unsafe,
            wall_clock_ms=wall_clock_ms, monotonic_ms=monotonic_ms,
        )
        if state["status"] == "REPEATED_FAILURE":
            _persist_replan(
                project, config, stage_id, payload, state, upstream_handoffs or [],
                requested_adapter, environ, resolver, replan_runner,
            )
            continue
        if state["status"] != "BACKOFF":
            return state
        now_ms = wall_clock_ms()
        require(type(now_ms) is int and now_ms >= state["last_wall_time_ms"],
                "Wall clock moved backwards")
        delay_ms = max(0, state["next_eligible_at_ms"] - now_ms)
        if delay_ms:
            sleeper(delay_ms / 1000)

def _persist_replan(
    project: Path, config: dict, stage_id: str, payload: dict, observed: dict,
    upstream_handoffs: list[dict], requested_adapter: str | None,
    environ: Mapping[str, str] | None, resolver: Callable[[str], str | None],
    replan_runner: ReplanRunner,
) -> None:
    """Serialize receipt creation and the authoritative READY transition."""
    with locked_stage_state(project, stage_id):
        current = load_stage_state(project, config, stage_id, payload)
        require(current == observed, "Stage state changed before replan")
        resumed = replan_runner(
            project, config, current, upstream_handoffs,
            requested_adapter=requested_adapter, environ=environ, resolver=resolver,
        )
        save_stage_state(project, resumed)


def run_persisted_attempt(
    project: Path, config: dict,
    stage_id: str, payload: dict,
    requested_adapter: str | None = None,
    environ: Mapping[str, str] | None = None,
    resolver: Callable[[str], str | None] = shutil.which,
    attempt_runner: AttemptRunner = run_stage_attempt,
    reserve_ms: int | None = None, resume_waiting: bool = False,
    acknowledge_unsafe: bool = False,
    wall_clock_ms: Clock | None = None,
    monotonic_ms: Clock | None = None,
) -> dict:
    """Run at most one attempt after durably reserving its budget."""
    project = Path(project).resolve()
    wall_clock_ms = wall_clock_ms or _wall_clock_ms
    monotonic_ms = monotonic_ms or _monotonic_ms
    with locked_stage_state(project, stage_id):
        state = _load_or_create(
            project, config, stage_id, payload, resume_waiting,
        )
        state = _preflight_state(
            project, config, state, requested_adapter, environ,
            resume_waiting, acknowledge_unsafe,
        )
        now_ms = wall_clock_ms()
        if state["status"] == "RUNNING":
            recovered = _recover_running(project, state, config, now_ms)
            save_stage_state(project, recovered)
            recovered = _record_attempt(project, config, recovered, requested_adapter, environ)
            return recovered
        if state["status"] == "STAGE_GREEN":
            state = _finish_manual_ack(project, state)
            state = _verify_attempt(project, config, state, requested_adapter, environ)
            return state
        if state["status"] not in {"READY", "BACKOFF"}:
            state = _verify_attempt(project, config, state, requested_adapter, environ)
            return state
        if state["status"] == "BACKOFF" and now_ms < state["next_eligible_at_ms"]:
            state = _verify_attempt(project, config, state, requested_adapter, environ)
            return state
        running = begin_attempt(
            state, config, now_ms, _reservation(state, config, reserve_ms),
            _workspace_digest(project),
        )
        save_stage_state(project, running)
        return _execute_attempt(
            project, config, stage_id, payload, running, requested_adapter,
            environ, resolver, attempt_runner, wall_clock_ms, monotonic_ms,
        )


def _preflight_state(
    project: Path, config: dict, state: dict, requested_adapter: str | None,
    environ: Mapping[str, str] | None, resume_waiting: bool,
    acknowledge_unsafe: bool,
) -> dict:
    require(type(resume_waiting) is bool, "Invalid waiting-state recovery flag")
    require(not acknowledge_unsafe or resume_waiting,
            "Safety acknowledgement requires waiting-state recovery")
    verify_artifact_history(project, config, state)
    if resume_waiting and state["status"] in RECOVERABLE_STATUSES:
        state = _resume_waiting(project, state, acknowledge_unsafe)
    if state["status"] == "BACKOFF":
        state = _verify_attempt(
            project, config, state, requested_adapter, environ,
        )
    return state


def _resume_waiting(project: Path, state: dict,
                    acknowledge_unsafe: bool) -> dict:
    unsafe = {"UNSAFE_CONTAINMENT", "WORKSPACE_TAINTED"}
    digest = _workspace_digest(project) if state["status"] in unsafe else None
    return resume_nonterminal_state(state, acknowledge_unsafe, digest)

def _recover_running(project: Path, state: dict, config: dict,
                     now_ms: int) -> dict:
    try:
        current_digest = _workspace_digest(project)
    except StoryLoopError:
        error = StoryLoopError(
            "Interrupted Stage workspace cannot be safely verified",
            code="WORKSPACE_TAINTED",
        )
        return settle_attempt_tainted(
            state, config, error, state["reserved_ms"], now_ms,
        )
    return recover_interrupted_attempt(state, config, now_ms, current_digest)

def _execute_attempt(
    project: Path,
    config: dict,
    stage_id: str,
    payload: dict,
    running: dict,
    requested_adapter: str | None,
    environ: Mapping[str, str] | None,
    resolver: Callable[[str], str | None],
    attempt_runner: AttemptRunner,
    wall_clock_ms: Clock,
    monotonic_ms: Clock,
) -> dict:
    started_ms = monotonic_ms()
    try:
        result = attempt_runner(
            project, config, stage_id, payload,
            requested_adapter=requested_adapter, environ=environ, resolver=resolver,
            attempt_deadline_ms=started_ms + running["reserved_ms"],
            budget_clock_ms=monotonic_ms,
        )
    except StoryLoopError as exc:
        failed = _settle_error(
            running, config, exc, _elapsed(started_ms, monotonic_ms()),
            wall_clock_ms(),
        )
        save_stage_state(project, failed)
        failed = _record_attempt(project, config, failed, requested_adapter, environ)
        return failed
    except BaseException:
        raise
    elapsed_ms = _elapsed(started_ms, monotonic_ms())
    if elapsed_ms > running["reserved_ms"]:
        failed = settle_attempt_failure(
            running, config,
            StoryLoopError("Stage exceeded its reserved time", code="TIME_BUDGET"),
            elapsed_ms, wall_clock_ms(),
        )
        save_stage_state(project, failed)
        failed = _record_attempt(project, config, failed, requested_adapter, environ)
        return failed
    result = _attach_pending_manual_ack(
        project, config, stage_id, payload, requested_adapter, environ, result,
    )
    settled = settle_attempt_success(
        running, config, result, elapsed_ms, wall_clock_ms(),
    )
    save_stage_state(project, settled)
    settled = _record_attempt(project, config, settled, requested_adapter, environ)
    return _finish_manual_ack(project, settled)

def _verify_attempt(project: Path, config: dict, state: dict,
                    requested_adapter: str | None,
                    environ: Mapping[str, str] | None) -> dict:
    return _commit_attempt(project, config, state, requested_adapter, environ)


def _record_attempt(project: Path, config: dict, state: dict,
                    requested_adapter: str | None,
                    environ: Mapping[str, str] | None) -> dict:
    """Bind audit evidence only after authoritative state is durable."""
    return _commit_attempt(project, config, state, requested_adapter, environ)


def _commit_attempt(project: Path, config: dict, state: dict,
                    requested_adapter: str | None,
                    environ: Mapping[str, str] | None) -> dict:
    auditable = {"BACKOFF", "REPEATED_FAILURE", "BUDGET_EXHAUSTED",
                 "WORKSPACE_TAINTED", "STAGE_GREEN"}
    if state["status"] not in auditable:
        return state
    adapter_id, _ = select_adapter(config, requested_adapter, environ)
    digest = verify_stage_attempt(project, config, state, adapter_id)
    bound = bind_artifact(state, digest)
    if bound != state:
        save_stage_state(project, bound)
    return bound

def _settle_error(state: dict, config: dict, error: StoryLoopError,
                  elapsed_ms: int, now_ms: int) -> dict:
    if elapsed_ms > state["reserved_ms"]:
        error = StoryLoopError("Stage exceeded its reserved time", code="TIME_BUDGET")
    if error.code == "WORKSPACE_TAINTED":
        settle = settle_attempt_tainted
    elif error.code in NONTERMINAL_STATUSES:
        settle = settle_attempt_nonterminal
    else:
        settle = settle_attempt_failure
    return settle(state, config, error, elapsed_ms, now_ms)

def _attach_pending_manual_ack(
    project: Path, config: dict, stage_id: str, payload: dict,
    requested_adapter: str | None, environ: Mapping[str, str] | None,
    result: dict,
) -> dict:
    expected = expected_manual_request_id(
        config, "implement", stage_id, payload, requested_adapter, environ,
    )
    if expected is None:
        return result
    acknowledgement = discover_manual_ack(project, stage_id, expected)
    if acknowledgement is None:
        return result
    response = result.get("agent_response")
    require(response is None or isinstance(response, dict), "Invalid Agent response")
    return {
        **result,
        "agent_response": {**(response or {}), "_manual_ack": acknowledgement},
    }

def _finish_manual_ack(project: Path, state: dict) -> dict:
    acknowledgement = state["pending_manual_ack"]
    if acknowledgement is None:
        return state
    consume_manual_ack(project, acknowledgement)
    cleared = {**state, "pending_manual_ack": None}
    save_stage_state(project, cleared)
    return cleared


def _load_or_create(project: Path, config: dict, stage_id: str,
                    payload: dict, resume_waiting: bool) -> dict:
    path = stage_state_path(project, stage_id)
    if path.exists():
        loader = load_stage_state_for_resume if resume_waiting else load_stage_state
        state = loader(project, config, stage_id, payload)
        mark_stage_initialized(project, stage_id)
        return state
    require(not stage_was_initialized(project, stage_id),
            "Initialized Stage state is missing; restore it before continuing")
    state = new_stage_state(project, config, stage_id, payload)
    save_stage_state(project, state)
    mark_stage_initialized(project, stage_id)
    return state

def _workspace_digest(project: Path) -> str:
    snapshot = capture_snapshot(
        project, include_control=True, include_ignored=True,
    )
    return snapshot_digest(snapshot)

def _reservation(state: dict, config: dict, requested: int | None) -> int:
    if requested is not None:
        return requested
    remaining = config["budgets"]["total_seconds"] * 1000 - state["consumed_ms"]
    require(remaining > 0, "Stage time budget exhausted")
    return min(config["agent"]["timeout_seconds"] * 1000, remaining)


def _elapsed(started_ms: int, finished_ms: int) -> int:
    require(type(started_ms) is int and type(finished_ms) is int,
            "Invalid monotonic clock value")
    require(finished_ms >= started_ms, "Monotonic clock moved backwards")
    return finished_ms - started_ms


def _wall_clock_ms() -> int:
    return time.time_ns() // 1_000_000


def _monotonic_ms() -> int:
    return time.monotonic_ns() // 1_000_000
