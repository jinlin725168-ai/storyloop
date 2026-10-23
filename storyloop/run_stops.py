"""Stop presentation and checkpointing above the deterministic run kernel."""
from pathlib import Path
from .run_state import save_run
from .run_transitions import transition
from .run_hints import next_step


def pause_for_boundary(
    project: Path, state: dict, boundary: dict, phase: str | None = None,
) -> dict:
    details = {
        "violations": boundary["violations"],
        "base_proposal": boundary["base_proposal"],
    }
    if phase is not None:
        details["phase"] = phase
    return pause(project, state, boundary["status"], **details)


def pause(project: Path, state: dict, status: str, **details) -> dict:
    """Persist an explicit stop with its details; the result always names the next command."""
    from .stop_report import build_stop

    transition(project, state, status, event="stop")
    persisted_details = {key: value for key, value in details.items() if key != "phase"}
    state.update(persisted_details)
    state["stop"] = build_stop(project, state, status, details)
    save_run(project, state)
    hint = (details.get("next") or next_step(status, state["change_id"], project, {**state, **details})
            or state["stop"]["resume"])
    return {"status": status, "change_id": state["change_id"], **details,
            "stop": state["stop"], "next": hint}
