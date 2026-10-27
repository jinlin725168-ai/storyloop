"""Open a base run without changing the approved contract or old run snapshots."""

from __future__ import annotations

from pathlib import Path

from . import __version__
from .base_upgrade import _changed_since
from .baseline import _git, load_baseline, load_project_config, verify_baseline
from .models import StoryLoopError, require
from .project_boundaries import audit_base_scope
from .project_git import current_branch, start_base
from .run_state import run_path, save_run
from .storage import IGNORED, load_json


def choose_implementer(scope: str, requested: str | None) -> str:
    """Select the one immutable execution mode for a new base run."""
    require(requested in {None, "human", "agent"}, "Invalid base implementer")
    require(not (scope == "ci" and requested == "agent"),
            "CI base changes require human implementation")
    return requested or "human"


def _self_hosted_agent(project: Path, paths: set[str]) -> bool:
    engine_file = Path(__file__).resolve()
    root = project.resolve()
    if not engine_file.is_relative_to(root):
        return False
    relative = engine_file.parent.relative_to(root).as_posix()
    installed = {name.casefold() for name in IGNORED} | {"site-packages", "dist-packages"}
    if any(part.casefold() in installed for part in relative.split("/")):
        return False
    tracked = _git(project, "ls-files", "--cached", "-z", "--", relative)
    return any(path.endswith(".py") for path in tracked.split("\0"))


def _open_state(project: Path, change: dict, implementer: str | None, new_run_state) -> dict:
    change_id = change["change_id"]
    saved = run_path(project, change_id)
    require(change["status"] in {"APPROVED", "PASSED"} and change["approval"],
            "Base change must be approved before execution")
    if saved.exists():
        return _resume_state(project, change, implementer, saved)
    return _start_state(project, change, implementer, new_run_state)


def _resume_state(project: Path, change: dict, implementer: str | None, saved: Path) -> dict:
    state = load_json(saved)
    mode = state.get("implementer", "human")
    require(state.get("contract_digest") == change["approval"]["contract_digest"]
            and mode in {"human", "agent"}, "Saved base run is invalid")
    require(implementer in {None, mode}, "Saved base implementer cannot change")
    expected = (state["source_branch"] if state["status"] in
                {"MERGED", "RECEIPTING", "FINALIZING", "PASSED"}
                or (state["status"] == "WORKSPACE_TAINTED"
                    and state.get("base_resume_status") == "FINALIZING")
                else state["feature_branch"])
    require(current_branch(project) == expected
            or (state["status"] == "VERIFIED" and current_branch(project) == state["source_branch"]),
            "Resume the saved StoryLoop base branch")
    return state


def _start_state(project: Path, change: dict, implementer: str | None, new_run_state) -> dict:
    change_id = change["change_id"]
    mode = choose_implementer(change["product"]["base"]["scope"], implementer)
    if mode == "agent":
        from .role_profiles import enabled, resolve_adapters, role_settings
        config = load_project_config(project)
        if enabled(config):
            adapter = role_settings(config, "implement", "heavy", base=True)["adapter"]
            if resolve_adapters(config)[adapter]["kind"] == "manual":
                raise StoryLoopError("Manual adapter cannot implement base changes in a "
                                     "disposable worktree; start with --implementer human "
                                     "or configure a command/Codex adapter", code="AGENT_UNAVAILABLE")
    require(change["approval"]["versions"]["engine_version"] == __version__,
            "Approved base engine version differs from the running engine")
    dirty = _git(project, "status", "--porcelain=v1", "--untracked-files=all", "-z")
    records = [item for item in dirty.split("\0") if item]
    expected = set(change["product"]["base"]["paths"])
    allowed = {item[3:].replace("\\", "/") for item in records}
    require(all(item[:2] in {" M", " D", "??"} for item in records)
            and allowed <= expected, "Prepared base files exceed approved paths")
    require(mode == "human" or not records,
            "Agent base implementation needs a clean source worktree; use --implementer human")
    require(mode != "agent" or not _self_hosted_agent(project, expected),
            "T6 self-hosted engine base changes need --implementer human or an independent old engine")
    audit_base_scope(project, change, expected)
    baseline = load_baseline(project)
    committed = _changed_since(project, baseline["source_head"])
    require(not committed.intersection(expected),
            "Committed proposal paths already exist on the source branch; "
            "revert them with a new commit, then replay the change on the new base branch")
    start = _base_start_context(project, change_id, baseline, allowed)
    state = new_run_state(project, change_id, start, change["approval"]["contract_digest"])
    state["implementer"] = mode
    state["engine_version"] = __version__
    state["engine_source"] = str(Path(__file__).resolve().parent)
    state["target_tree_oid"] = _git(project, "rev-parse", f"{state['source_head']}^{{tree}}").strip()
    save_run(project, state)
    return state


def _base_start_context(project: Path, change_id: str, baseline: dict,
                        allowed: set[str]) -> dict:
    branch = f"storyloop/{change_id}"
    if current_branch(project) == branch:
        head = _git(project, "rev-parse", "HEAD").strip()
        require(head == _git(project, "rev-parse", baseline["source_branch"]).strip(),
                "Unrecorded base branch no longer matches the locked source")
        verify_baseline(project, resume=True, allowed_base_paths=allowed)
        return {"status": "STARTED", "branch": branch,
                "source_branch": baseline["source_branch"], "source_head": head}
    verify_baseline(project, allowed_base_paths=allowed)
    return start_base(project, change_id, allowed)
