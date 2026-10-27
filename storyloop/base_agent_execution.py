"""Agent writes one approved base candidate; the engine owns its Git commit."""

from __future__ import annotations

from pathlib import Path
import hashlib

from .baseline import _git
from .base_agent_transfer import AGENT_EMAIL, AGENT_NAME, transfer_agent_candidate  # noqa: F401  # 兼容再导出
from .base_agent_worktree import create_agent_worktree, remove_agent_worktree
from .base_design import invalid_reason
from .engine_source_safety import ENGINE_WARNING, engine_source_digest, engine_warning
from .guarded_calls import write_safely
from .models import StoryLoopError, require
from .project_agents import invoke_project_agent
from .run_state import save_run
from .run_stops import pause


def agent_control_binding(project: Path, change_id: str) -> dict[str, str]:
    """Bind the configuration files an agent must never edit during its call."""
    from .project_profile import BASE_BUILD_SUPPLEMENT
    relatives = [".storyloop/config.json", ".storyloop/baseline.json",
                 ".storyloop/state/adapters.json", BASE_BUILD_SUPPLEMENT,
                 f".storyloop/base-proposals/{change_id}.json"]
    return {relative: (hashlib.sha256((project / relative).read_bytes()).hexdigest()
                       if (project / relative).is_file() and not (project / relative).is_symlink()
                       else "<missing-or-unsafe>") for relative in relatives}


def agent_binding_changes(project: Path, change_id: str, saved: dict | None) -> set[str]:
    """Ignore the retired whole-file Git config hash in an in-flight agent state."""
    current = agent_control_binding(project, change_id)
    prior = {key: value for key, value in (saved or {}).items() if key != ".git/config"}
    return {key for key in set(prior) | set(current) if prior.get(key) != current.get(key)}


def implement_base_candidate(project: Path, change: dict, state: dict, services=None) -> dict:
    """Persist the call anchor before granting the agent its approved path set."""
    require(invalid_reason(project, change, state) is None,
            "Approved base design changed before agent implementation")
    checkout = _prepare_agent_call(project, change, state)
    try:
        _invoke_agent(project, checkout, change, state, services)
        committed = transfer_agent_candidate(project, checkout, change, state["contract_commit"])
    except StoryLoopError as exc:
        remove_agent_worktree(project, state)
        save_run(project, state)
        return pause(project, state, "WORKSPACE_TAINTED", reason=str(exc),
                     next="Inspect the main worktree, then storyloop reset <project> --change "
                          f"{change['change_id']} --reason '<reason>'")
    _save_agent_candidate(project, change, state, committed)
    remove_agent_worktree(project, state)
    save_run(project, state)
    return {"status": "IMPLEMENTING", "change_id": change["change_id"]}


def _prepare_agent_call(project: Path, change: dict, state: dict) -> Path:
    from .adapter_capabilities import ensure_checked
    from .baseline import load_project_config
    from .role_profiles import enabled
    state["base_inflight"] = "agent_capability_check"
    save_run(project, state)
    if enabled(load_project_config(project)):
        ensure_checked(project)
    head = _git(project, "rev-parse", "HEAD").strip()
    require(head == state["contract_commit"]
            and not _git(project, "status", "--porcelain=v1", "--untracked-files=all").strip(),
            "Agent base implementation needs a clean contract commit")
    state["agent_call_head"] = head
    state["agent_control_binding"] = agent_control_binding(project, change["change_id"])
    try:
        state["agent_engine_digest"] = engine_source_digest(Path(state["engine_source"]))
    except (OSError, StoryLoopError):
        state["agent_engine_digest"] = None
        state["engine_warning"] = ENGINE_WARNING
    state["base_inflight"] = "agent_implementation"
    return create_agent_worktree(project, state)


def _invoke_agent(project: Path, checkout: Path, change: dict, state: dict, services) -> None:
    payload = {"mode": "base-implement", "change": change,
               "approved_paths": change["product"]["base"]["paths"],
               "design_plan": state["base_design"], "contract_commit": state["contract_commit"]}
    operation = (lambda: services.implement(checkout, payload)) if services else (
        lambda: invoke_project_agent(project, "implement", payload, workspace=checkout))
    def source_guard() -> None:
        warning = engine_warning(state)
        if warning:
            state["engine_warning"] = warning
        if agent_binding_changes(project, change["change_id"], state["agent_control_binding"]):
            raise StoryLoopError("Base agent changed protected StoryLoop control files; inspect "
                                 "them before running again", code="WORKSPACE_TAINTED")
    approved = set(change["product"]["base"]["paths"])
    excluded = (frozenset({"openspec/principles.json"})
                if change["product"]["base"]["scope"] == "knowledge"
                and "openspec/principles.json" in approved else frozenset())
    response, _ = write_safely(project, change["change_id"], operation, source_guard, excluded)
    save_run(project, state)
    require(not response.get("questions"), "Base implementer left unresolved questions")


def _save_agent_candidate(project: Path, change: dict, state: dict, committed: str) -> None:
    from .baseline import load_project_config
    from .call_context import capability_for
    from .role_profiles import enabled, role_settings
    config = load_project_config(project)
    adapter = role_settings(config, "implement", "heavy", base=True)["adapter"] if enabled(config) else None
    state["agent_adapter"] = adapter
    state["agent_vendor"] = capability_for(project, adapter).get("vendor") if adapter else None
    state["candidate_commit"] = committed
    state["base_inflight"] = "candidate_verification"
    save_run(project, state)
