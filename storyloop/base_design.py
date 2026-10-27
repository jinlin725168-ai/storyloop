"""Engine-bound design proposal and named approval for an agent base run."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .agent_transport import conform
from .baseline import _git
from .changes import contract_digest, load_change
from .engine_source_safety import ENGINE_WARNING, engine_source_digest, engine_warning
from .guarded_calls import read_only_call
from .models import StoryLoopError, require, text
from .project_agents import invoke_project_agent
from .project_git import current_branch
from .role_schemas import schema_for
from .run_state import run_path, save_run
from .run_stops import pause
from .run_transitions import transition
from .storage import load_json, locked, write_json


def design_path(project: Path, change_id: str) -> Path:
    return project / ".storyloop/state/base-designs" / f"{change_id}.json"


def approval_path(project: Path, change_id: str) -> Path:
    return project / ".storyloop/state/base-design-approvals" / f"{change_id}.json"


def _digest(value: dict) -> str:
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False,
                     separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _binding(project: Path, change: dict, state: dict) -> dict:
    return {"contract_digest": contract_digest(change["product"]),
            "contract_commit": state["contract_commit"],
            "source_head": state["source_head"],
            "baseline_version": change["approval"]["versions"]["baseline_version"],
            "engine_version": __version__}


def _require_unimplemented(project: Path, state: dict) -> None:
    require(current_branch(project) == state["feature_branch"]
            and _git(project, "rev-parse", "HEAD").strip() == state["contract_commit"]
            and not _git(project, "status", "--porcelain=v1", "--untracked-files=all").strip(),
            "Base design needs a clean contract candidate before approval")


def _trusted(project: Path, state: dict, kind: str) -> dict | None:
    path = design_path(project, state["change_id"]) if kind == "base_design" else approval_path(
        project, state["change_id"])
    expected = state.get(kind + "_sha256")
    if not isinstance(expected, str) or not path.is_file():
        return None
    try:
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            return None
        value = load_json(path)
    except (OSError, ValueError):
        return None
    return value if value == state.get(kind) else None


def invalid_reason(project: Path, change: dict, state: dict) -> str | None:
    plan_path, approved_path = design_path(project, state["change_id"]), approval_path(
        project, state["change_id"])
    plan = _trusted(project, state, "base_design")
    if plan is None or plan.get("binding") != _binding(project, change, state):
        return "Base design differs from the saved attempt"
    if state.get("base_design_approval_sha256") or approved_path.exists():
        approval = _trusted(project, state, "base_design_approval")
        if approval is None or approval.get("plan_id") != plan.get("plan_id"):
            return "Base design approval differs from the saved attempt"
    if not plan_path.is_file():
        return "Base design is missing"
    return None


def propose_design(project: Path, change: dict, state: dict, services=None) -> dict:
    state["base_inflight"] = "base_design"
    try:
        state["agent_engine_digest"] = engine_source_digest(Path(state["engine_source"]))
    except (OSError, StoryLoopError):
        state["agent_engine_digest"] = None
        state["engine_warning"] = ENGINE_WARNING
    save_run(project, state)
    payload = {"mode": "base-plan", "change": change,
               "contract_commit": state["contract_commit"],
               "approved_paths": change["product"]["base"]["paths"]}
    operation = (lambda: services.design(project, payload)) if services and services.design else (
        lambda: invoke_project_agent(project, "design", payload))
    def source_guard() -> None:
        warning = engine_warning(state)
        if warning:
            state["engine_warning"] = warning
    try:
        answer = read_only_call(project, change["change_id"], operation,
                                post_call_guard=source_guard)
    except StoryLoopError as exc:
        if exc.code != "WORKSPACE_TAINTED":
            raise
        return pause(project, state, "WORKSPACE_TAINTED", reason=str(exc),
                     next="Inspect and reinstall the StoryLoop engine before rerunning")
    cleaned, errors = conform(answer, schema_for("design", payload))
    require(not errors and not cleaned.get("questions"),
            "Base design needs complete steps, risks and rollback without unresolved questions")
    plan = {"version": 1, "change_id": change["change_id"], "binding": _binding(project, change, state),
            "goal": change["product"]["goal"], "paths": change["product"]["base"]["paths"],
            **{key: cleaned[key] for key in ("summary", "steps", "risks", "rollback")}}
    plan["plan_id"] = _digest(plan)
    path = design_path(project, change["change_id"])
    write_json(path, plan, max_bytes=1_000_000)
    state["base_design"] = plan
    state["base_design_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    state.pop("base_inflight", None)
    save_run(project, state)
    return pause(project, state, "DESIGN_APPROVAL_REQUIRED",
                 next=f"storyloop base-design-approve <project> --change {change['change_id']} "
                      f"--plan-id {plan['plan_id']}")


def approve_base_design(project: Path, change_id: str, plan_id: str) -> dict:
    """Record a named human decision under the same lock as run and reset."""
    project = Path(project).resolve()
    with locked(project):
        change = load_change(project, change_id)
        state = load_json(run_path(project, change_id))
        require(state.get("implementer") == "agent"
                and state.get("status") == "DESIGN_APPROVAL_REQUIRED",
                "Base design approval needs an agent design stop")
        require(invalid_reason(project, change, state) is None, "Base design changed before approval")
        _require_unimplemented(project, state)
        plan = state["base_design"]
        require(plan_id == plan["plan_id"], "Stale base plan id")
        actor = text(_git(project, "config", "user.name").strip(), "base design approver")
        approval = {"version": 1, "change_id": change_id, "plan_id": plan_id,
                    "binding": plan["binding"], "actor": actor,
                    "approved_at": datetime.now(timezone.utc).isoformat()}
        path = approval_path(project, change_id)
        write_json(path, approval, max_bytes=1_000_000)
        state["base_design_approval"] = approval
        state["base_design_approval_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        save_run(project, state)
        return {"status": "DESIGN_APPROVED", **approval}


def resume_design(project: Path, change: dict, state: dict) -> dict:
    if not state.get("base_design_approval"):
        return {"status": "DESIGN_APPROVAL_REQUIRED", "change_id": change["change_id"],
                "next": f"storyloop base-design-approve <project> --change {change['change_id']} "
                        f"--plan-id {state['base_design']['plan_id']}"}
    require(invalid_reason(project, change, state) is None, "Base design approval is stale")
    _require_unimplemented(project, state)
    transition(project, state, "IMPLEMENTING", event="begin_attempt")
    state.pop("stop", None)
    state.pop("next", None)
    save_run(project, state)
    return {"status": "IMPLEMENTING", "change_id": change["change_id"],
            "next": f"storyloop run <project> --change {change['change_id']}"}
