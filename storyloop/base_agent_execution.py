"""Agent writes one approved base candidate; the engine owns its Git commit."""

from __future__ import annotations

from pathlib import Path
import hashlib

from .base_agent_transfer import AGENT_EMAIL, AGENT_NAME, transfer_agent_candidate  # noqa: F401  # 兼容再导出
from .base_agent_worktree import agent_cleanup_next, create_agent_worktree, remove_agent_worktree
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
    try:
        checkout = _prepare_agent_call(project, change, state)
    except (StoryLoopError, OSError) as exc:
        if isinstance(exc, StoryLoopError) and exc.code == "AGENT_UNAVAILABLE":
            raise
        return _pause_agent_issue(project, change, state, str(exc))
    try:
        _invoke_agent(project, checkout, change, state, services)
        committed = transfer_agent_candidate(project, state, change)
    except (StoryLoopError, OSError) as exc:
        return _cleanup_and_pause(project, change, state, str(exc))
    _save_agent_candidate(project, change, state, committed)
    try:
        remove_agent_worktree(project, state)
    except (StoryLoopError, OSError) as exc:
        state["interrupted_status"] = "IMPLEMENTING"
        return _pause_agent_issue(project, change, state, str(exc),
                                  invalid_anchor=isinstance(exc, StoryLoopError)
                                  and exc.code == "AGENT_ANCHOR_INVALID")
    save_run(project, state)
    return {"status": "IMPLEMENTING", "change_id": change["change_id"]}


def _cleanup_and_pause(project: Path, change: dict, state: dict, reason: str) -> dict:
    invalid_anchor = False
    try:
        remove_agent_worktree(project, state)
    except (StoryLoopError, OSError) as cleanup:
        invalid_anchor = (isinstance(cleanup, StoryLoopError)
                          and cleanup.code == "AGENT_ANCHOR_INVALID")
        reason = f"{reason}; agent worktree cleanup failed: {cleanup}"
    return _pause_agent_issue(project, change, state, reason, invalid_anchor=invalid_anchor)


def _pause_agent_issue(project: Path, change: dict, state: dict, reason: str,
                       invalid_anchor: bool = False) -> dict:
    if invalid_anchor:
        next_step = ("Preserve the worktree and inspect its saved anchor and Git registration; "
                     f"then {agent_cleanup_next({**state, 'base_inflight': None})}")
    elif state.get("candidate_commit") and state.get("base_inflight") == "candidate_verification":
        next_step = ("Close worktree file holders, then "
                     f"{agent_cleanup_next(state)}; the saved candidate will be retained")
    else:
        next_step = ("Inspect the main worktree and saved agent worktree anchor, then "
                     f"storyloop reset <project> --change {change['change_id']} "
                     "--reason '<reason>'")
    return pause(project, state, "WORKSPACE_TAINTED", reason=reason,
                 next=next_step)


def _prepare_agent_call(project: Path, change: dict, state: dict) -> Path:
    from .adapter_capabilities import ensure_checked
    from .baseline import load_project_config
    from .role_profiles import enabled, resolve_adapters, role_settings
    config = load_project_config(project)
    if enabled(config):
        adapter = role_settings(config, "implement", "heavy", base=True)["adapter"]
        if resolve_adapters(config)[adapter]["kind"] == "manual":
            raise StoryLoopError("Manual adapter cannot implement base changes in a disposable "
                                 "worktree; use --implementer human for a new run or configure "
                                 "a command/Codex adapter", code="AGENT_UNAVAILABLE")
    state["base_inflight"] = "agent_capability_check"
    save_run(project, state)
    if enabled(config):
        ensure_checked(project)
    state["agent_control_binding"] = agent_control_binding(project, change["change_id"])
    try:
        state["agent_engine_digest"] = engine_source_digest(Path(state["engine_source"]))
    except (OSError, StoryLoopError, UnicodeError):
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
    response, changes = write_safely(project, change["change_id"], operation, source_guard,
                                     excluded, exclude_root_git_snapshot=True,
                                     allowed_git_worktree_attempt=state["agent_worktree"]["attempt_id"])
    if changes:
        paths = ", ".join(item.path for item in changes[:20])
        raise StoryLoopError(f"Agent changed main project during isolated implementation: {paths}",
                             code="WORKSPACE_TAINTED")
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
