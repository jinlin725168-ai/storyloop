"""Stop presentation and checkpointing above the deterministic run kernel."""
from contextlib import contextmanager
from pathlib import Path
from .errors import StoryLoopError
from .processutil import redact
from .run_state import DISPLAY_STOPS as STOP_STATUSES, save_run
from .run_transitions import transition
from .run_hints import next_step


def pause_for_boundary(
    project: Path, state: dict, boundary: dict, phase: str | None = None,
) -> dict:
    details = {
        "violations": boundary["violations"],
        "base_proposal": boundary["base_proposal"],
    }
    if boundary.get("ci_requires_human_proposal"):
        details["ci_requires_human_proposal"] = True
    if phase is not None:
        details["phase"] = phase
    return pause(project, state, boundary["status"], **details)


def pause(project: Path, state: dict, status: str, **details) -> dict:
    """Persist an explicit stop with its details; the result always names the next command."""
    from .stop_report import build_stop

    if state.get("engine_warning"):
        details.setdefault("warning", state["engine_warning"])

    transition(project, state, status, event="stop")
    persisted_details = {key: value for key, value in details.items() if key != "phase"}
    state.update(persisted_details)
    state["stop"] = build_stop(project, state, status, details)
    save_run(project, state)
    convergence = details.get("convergence") or {}
    hint = (state["stop"]["resume"] if convergence.get("exhausted") else
            details.get("next") or next_step(status, state["change_id"], project, {**state, **details})
            or state["stop"]["resume"])
    return {"status": status, "change_id": state["change_id"], **details,
            "stop": state["stop"], "next": hint}


def clear_blocked(project: Path, run: dict | None) -> dict | None:
    """Drop a previous run's BLOCKED stop and its marker from a loaded run file and persist that."""
    if run and ((run.get("stop") or {}).get("status") == "BLOCKED" or "blocked_from" in run):
        run.pop("stop", None)
        run.pop("blocked_from", None)
        save_run(project, run)
    return run


@contextmanager
def saving_blocked_stop(project: Path, change_id: str):
    """Save a BLOCKED stop on the opened run without changing its status, then re-raise."""
    try:
        yield
    except StoryLoopError as exc:
        if exc.code == "BLOCKED":
            _save_blocked(project, change_id, exc)
        raise


def _save_blocked(project: Path, change_id: str, exc: StoryLoopError) -> None:
    from .run_state import run_path
    from .stop_report import build_stop
    from .storage import load_json
    try:
        state = load_json(run_path(project, change_id))
        if state.get("status") == "PASSED" or state.get("status") in STOP_STATUSES:
            return
        state["stop"] = build_stop(project, state, "BLOCKED", {"error": redact(str(exc))})
        state["blocked_from"] = state["status"]
        save_run(project, state)
    except (OSError, ValueError, TypeError, KeyError, StoryLoopError):
        return  # Saving the report must never mask the engine's original error.
