"""Human base execution through the shared run state and transition guards."""

from __future__ import annotations

from pathlib import Path

from .approvals import materialize_approved_change
from .base_upgrade import base_candidate_paths, establish_old_green
from .baseline import _git, verify_baseline
from .models import require
from .project_git import current_branch, start_base
from .project_loop_support import relative_files
from .project_preparation import _ensure_contract_commit
from .run_state import run_path, save_run
from .run_stops import pause
from .run_transitions import transition
from .storage import load_json


def run_base_change(project: Path, change: dict, implementer: str | None, new_run_state) -> dict:
    """Open or resume the manually implemented base change without an agent call."""
    state = _open_state(project, change, implementer, new_run_state)
    if state["status"] in {"PREPARING", "CONTRACT_READY", "CONTRACT_COMMITTED"}:
        return _prepare(project, change, state)
    if state["status"] == "HUMAN_IMPLEMENTATION_REQUIRED":
        base_candidate_paths(project, change, state)
        candidate = _git(project, "rev-parse", "HEAD").strip()
        transition(project, state, "IMPLEMENTING", event="human_candidate_ready",
                   updates={"candidate_commit": candidate})
        save_run(project, state)
    return {"status": state["status"], "change_id": change["change_id"]}


def _open_state(project: Path, change: dict, implementer: str | None, new_run_state) -> dict:
    change_id = change["change_id"]
    saved = run_path(project, change_id)
    require(change["status"] == "APPROVED" and change["approval"],
            "Base change must be approved before execution")
    if saved.exists():
        state = load_json(saved)
        require(state.get("contract_digest") == change["approval"]["contract_digest"]
                and state.get("implementer") == "human", "Saved base run is invalid")
        require(implementer in {None, "human"}, "Base agent implementation belongs to E2")
        require(current_branch(project) == state["feature_branch"],
                "Resume the saved StoryLoop base branch")
        return state
    require(implementer == "human", "Use --implementer human for E1 base changes")
    verify_baseline(project)
    state = new_run_state(project, change_id, start_base(project, change_id),
                          change["approval"]["contract_digest"])
    state["implementer"] = "human"
    save_run(project, state)
    return state


def _prepare(project: Path, change: dict, state: dict) -> dict:
    change_id = change["change_id"]
    contract_root = materialize_approved_change(project, change_id)
    paths = relative_files(project, [contract_root])
    if state["status"] == "PREPARING":
        transition(project, state, "CONTRACT_READY", event="contract_ready")
        save_run(project, state)
    if state["status"] == "CONTRACT_READY":
        commit = _ensure_contract_commit(project, state, paths)
        transition(project, state, "CONTRACT_COMMITTED", event="contract_committed",
                   updates={"contract_commit": commit})
        save_run(project, state)
    establish_old_green(project, change, state)
    transition(project, state, "IMPLEMENTING", event="base_green_ready")
    save_run(project, state)
    return pause(project, state, "HUMAN_IMPLEMENTATION_REQUIRED",
                 next=f"Commit the approved paths on {state['feature_branch']}, then "
                      f"storyloop run <project> --change {change_id}")
