"""Agent writes one approved base candidate; the engine owns its Git commit."""

from __future__ import annotations

from pathlib import Path
import hashlib

from .baseline import _git
from .base_design import invalid_reason
from .guarded_calls import write_safely
from .models import StoryLoopError, require
from .project_agents import invoke_project_agent
from .project_boundaries import _changed_paths, audit_base_scope
from .project_git import commit_paths
from .run_state import save_run
from .run_stops import pause

AGENT_NAME = "StoryLoop Agent"
AGENT_EMAIL = "agent@storyloop.local"


def agent_control_binding(project: Path, change_id: str) -> dict[str, str]:
    """Bind the configuration files an agent must never edit during its call."""
    from .project_profile import BASE_BUILD_SUPPLEMENT
    relatives = [".storyloop/config.json", ".storyloop/baseline.json",
                 BASE_BUILD_SUPPLEMENT, f".storyloop/base-proposals/{change_id}.json"]
    return {relative: (hashlib.sha256((project / relative).read_bytes()).hexdigest()
                       if (project / relative).is_file() and not (project / relative).is_symlink()
                       else "<missing-or-unsafe>") for relative in relatives}


def agent_candidate_paths(project: Path, change: dict, touched: list) -> list[str]:
    """Match both the snapshot and Git views to the human-approved exact paths."""
    expected = set(change["product"]["base"]["paths"])
    seen = {item.path for item in touched if not item.path.endswith("/")}
    actual = set(_changed_paths(project))
    unexpected = sorted((seen | actual) - expected)
    if unexpected:
        raise StoryLoopError("Agent wrote outside approved base paths: " + ", ".join(unexpected),
                             code="WORKSPACE_TAINTED")
    if actual != expected:
        raise StoryLoopError("Agent did not implement every approved base path",
                             code="WORKSPACE_TAINTED")
    for path in actual:
        target = project / path
        if target.is_symlink() or (hasattr(target, "is_junction") and target.is_junction()):
            raise StoryLoopError(f"Agent created unsafe link: {path}", code="WORKSPACE_TAINTED")
    audit_base_scope(project, change, actual)
    return sorted(actual)


def implement_base_candidate(project: Path, change: dict, state: dict, services=None) -> dict:
    """Persist the call anchor before granting the agent its approved path set."""
    require(invalid_reason(project, change, state) is None,
            "Approved base design changed before agent implementation")
    head = _git(project, "rev-parse", "HEAD").strip()
    require(head == state["contract_commit"]
            and not _git(project, "status", "--porcelain=v1", "--untracked-files=all").strip(),
            "Agent base implementation needs a clean contract commit")
    state["agent_call_contract"] = state["contract_commit"]
    state["agent_call_head"] = head
    state["agent_control_binding"] = agent_control_binding(project, change["change_id"])
    state["base_inflight"] = "agent_implementation"
    save_run(project, state)
    payload = {"mode": "base-implement", "change": change,
               "approved_paths": change["product"]["base"]["paths"],
               "design_plan": state["base_design"], "contract_commit": head}
    operation = (lambda: services.implement(project, payload)) if services else (
        lambda: invoke_project_agent(project, "implement", payload))
    try:
        response, touched = write_safely(project, change["change_id"], operation)
        require(not response.get("questions"), "Base implementer left unresolved questions")
        paths = agent_candidate_paths(project, change, touched)
    except StoryLoopError as exc:
        if exc.code == "WORKSPACE_TAINTED" or _git(
                project, "status", "--porcelain=v1", "--untracked-files=all").strip():
            return pause(project, state, "WORKSPACE_TAINTED", reason=str(exc),
                         next="Inspect the agent changes, then storyloop reset <project> --change "
                              f"{change['change_id']} --reason '<reason>'")
        raise
    committed = commit_paths(project, paths,
                             f"feat({change['change_id']}): implement approved base change",
                             identity=(AGENT_NAME, AGENT_EMAIL))
    state["candidate_commit"] = committed["commit"]
    state["base_inflight"] = "candidate_verification"
    save_run(project, state)
    return {"status": "IMPLEMENTING", "change_id": change["change_id"]}
