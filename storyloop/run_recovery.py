"""Explicit human recovery of a stuck product run, with an audit trail."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .changes import load_change
from .change_types import type_of
from .baseline import _git
from .git_command import git_is_ancestor
from .models import StoryLoopError, identifier, require, text
from .path_security import safe_target
from .project_git import changed_paths, current_branch
from .storage import load_json, locked
from .run_state import save_run
from .run_transitions import transition, RESETTABLE
from .run_guards import base_report_paths, reset_status as _reset_status

Clock = Callable[[], datetime]
# Terminal or wedged states a human may reset. Everything else either resumes by itself
# (`storyloop run`) or must go through its own command (accept, answer, base-update).
STALE_KEYS = ("error", "questions", "pending", "verified", "candidate_sha256", "next", "stop")
BASE_PREMERGE = frozenset({"PREPARING", "CONTRACT_READY", "CONTRACT_COMMITTED", "IMPLEMENTING",
                           "HUMAN_IMPLEMENTATION_REQUIRED", "DESIGN_APPROVAL_REQUIRED",
                           "BASE_REVIEW_REQUIRED",
                           "MANUAL_ACCEPTANCE_REQUIRED", "VERIFIED", "WORKSPACE_TAINTED"})
BASE_ATTEMPT_KEYS = ("old_green_sha256", "base_evidence", "base_evidence_sha256",
                     "base_design", "base_design_sha256", "base_design_approval",
                     "base_design_approval_sha256",
                     "base_review", "base_review_sha256", "manual_acceptance_sha256",
                     "candidate_commit", "candidate_sha256", "base_inflight",
                     "implementation_commit", "report_commit", "report_sha256",
                     "acceptance_evidence_sha256", "review", "stop", "next", "error", "pending",
                     "interrupted_status", "base_resume_status", "baseline_issue")


def reset_run(project: Path, change_id: str, reason: str, now: Clock | None = None) -> dict:
    """Reset one run while holding the same project lock as storyloop run."""
    project = Path(project).resolve()
    change_id = identifier(change_id, "change id")
    reason = text(reason, "reset reason")
    with locked(project):
        return _reset_locked(project, change_id, reason, now)


def _reset_locked(project: Path, change_id: str, reason: str, now: Clock | None) -> dict:
    change = load_change(project, change_id)
    path = project / ".storyloop/state/runs" / f"{change_id}.json"
    require(path.exists(), "There is no run to reset for this change")
    state = load_json(path)
    require(state.get("change_id") == change_id, "Run state belongs to another change")
    if type_of(change["product"]) == "base":
        return _reset_base(project, change, state, reason, now)
    require(state.get("status") in RESETTABLE,
            f"Only {', '.join(sorted(RESETTABLE))} runs can be reset (current: {state.get('status')})")
    require(state.get("contract_digest") == (change.get("approval") or {}).get("contract_digest"),
            "Run state belongs to a different approved contract; approve and run again instead")
    require(current_branch(project) == state.get("feature_branch"),
            "Check out the run's feature branch before resetting")
    moment = (now or _utc_now)().astimezone(timezone.utc).isoformat(timespec="seconds")
    record = {"at": moment, "from_status": state["status"], "attempt": int(state.get("attempt", 0)),
              "reason": reason}
    resume_status = _reset_status(state)
    transition(project, state, resume_status, event="reset",
               effect=lambda: _reset_fields(state, resume_status))
    state.setdefault("resets", []).append(record)
    save_run(project, state)
    return {"status": resume_status, "change_id": change_id, "reset": record,
            "next": f"storyloop run <project> --change {change_id}"}


def _reset_base(project: Path, change: dict, state: dict, reason: str,
                now: Clock | None) -> dict:
    require(state.get("status") in BASE_PREMERGE
            and state.get("base_resume_status") != "FINALIZING"
            and not base_source_contains_candidate(project, state),
            "Merged base runs cannot reset; resume finalization with storyloop run")
    require(state.get("contract_digest") == change["approval"]["contract_digest"],
            "Run state belongs to a different approved contract")
    require(current_branch(project) == state.get("feature_branch"),
            "Check out the saved base branch before resetting")
    record = {"at": (now or _utc_now)().astimezone(timezone.utc).isoformat(timespec="seconds"),
              "from_status": state["status"], "attempt": int(state.get("attempt", 0)),
              "reason": reason}
    target = restart_base_attempt(project, state)
    state.setdefault("resets", []).append(record)
    save_run(project, state)
    return {"status": target, "change_id": state["change_id"], "reset": record,
            "next": f"storyloop run <project> --change {state['change_id']}"}


def restart_base_attempt(project: Path, state: dict, rejected: bool = False) -> str:
    """Discard only engine artifacts; leave the human's committed implementation intact."""
    require(state.get("contract_commit"), "Base restart needs the committed contract")
    require(not base_source_contains_candidate(project, state),
            "Merged base runs cannot restart; resume finalization with storyloop run")
    require(current_branch(project) == state.get("feature_branch"),
            "Resume the saved StoryLoop base branch before restarting")
    candidate = state.get("candidate_commit")
    head = _git(project, "rev-parse", "HEAD").strip()
    if head == state["contract_commit"]:
        candidate = None
    if candidate and head != candidate:
        if _report_tail(project, state, candidate, head):
            _git(project, "reset", "--soft", candidate)
            head = candidate
        else:
            require(git_is_ancestor(project, state["contract_commit"], head)
                    and not _has_report_history(project, state, head),
                    "Base branch contains mixed human and engine commits; preserve them manually")
            candidate = head
    if not candidate and head != state["contract_commit"]:
        require(git_is_ancestor(project, state["contract_commit"], head)
                and not _has_report_history(project, state, head),
                "Base branch contains mixed human and engine commits; preserve them manually")
        candidate = head  # The human committed before the engine saved candidate state.
    target = "HUMAN_IMPLEMENTATION_REQUIRED" if candidate else "CONTRACT_COMMITTED"
    _git(project, "reset", "-q", "--", *sorted(base_report_paths(state)))
    _discard_base_files(project, state)
    transition(project, state, target, event="base_restart")
    for key in BASE_ATTEMPT_KEYS:
        state.pop(key, None)
    if rejected:
        state["rejected_candidate_commit"] = candidate
    save_run(project, state)
    return target


def base_source_contains_candidate(project: Path, state: dict) -> bool:
    """A source ref containing this candidate or report is already past the restart boundary."""
    source = state.get("source_branch")
    if not isinstance(source, str) or not source:
        return False
    source_ref = f"refs/heads/{source}"
    try:
        return any(git_is_ancestor(project, commit, source_ref)
                   for commit in (state.get("report_commit"), state.get("candidate_commit"))
                   if isinstance(commit, str) and commit)
    except StoryLoopError as exc:
        raise StoryLoopError(
            f"Cannot inspect base source {source}: source branch or saved candidate object "
            "is missing; restore the ref/object before resuming this run",
            code="BASE_SOURCE_UNAVAILABLE",
        ) from exc


def manual_base_merge_error(state: dict, situation: str) -> str:
    """Explain the human-only path after an unsupported manual source merge."""
    source = state["source_branch"]
    change_id = state["change_id"]
    return (f"Base run cannot continue: {situation}. Preserve shared history; for an "
            f"unshared source, restore it with: git switch {source}; "
            f"git branch backup/{change_id}-manual-merge; "
            f"git reset --hard {state['source_head']}; "
            f"git switch {state['feature_branch']}; "
            f"storyloop run <project> --change {change_id}. "
            "If the source is shared, coordinate a new base change instead of rewriting it.")


def _has_report_history(project: Path, state: dict, head: str) -> bool:
    paths = sorted(base_report_paths(state))
    history = _git(project, "log", "--format=", "--name-only", "--no-renames",
                   f"{state['contract_commit']}..{head}", "--", *paths)
    return bool(history.strip())


def _report_tail(project: Path, state: dict, candidate: str, head: str) -> bool:
    """Only engine report commits may be removed from the human branch."""
    allowed = base_report_paths(state)
    subject = f"docs({state['change_id']}): "
    current = head
    for _ in range(8):
        parents = _git(project, "rev-list", "--parents", "-n", "1", current).split()
        if len(parents) != 2 or not changed_paths(project, parents[1], current) <= allowed:
            return False
        message = _git(project, "log", "-1", "--format=%s", current).strip()
        if not message.startswith(subject) or "base verification" not in message:
            return False
        if parents[1] == candidate:
            return True
        current = parents[1]
    return False


def _discard_base_files(project: Path, state: dict) -> None:
    change_id = state["change_id"]
    relatives = [f".storyloop/evidence/{change_id}/base-old-green.json",
                 f".storyloop/evidence/{change_id}/base-candidate-green.json",
                 f".storyloop/state/base-designs/{change_id}.json",
                 f".storyloop/state/base-design-approvals/{change_id}.json",
                 f".storyloop/state/base-reviews/{change_id}.json",
                 f".storyloop/state/acceptance/{change_id}.json",
                 *sorted(base_report_paths(state))]
    for relative in relatives:
        safe_target(project, relative, "base attempt artifact").unlink(missing_ok=True)
    design_dir = project / ".storyloop/state/base-designs"
    for temporary in design_dir.glob(f"{change_id}.json.*.tmp"):
        safe_target(project, temporary.relative_to(project).as_posix(),
                    "base design temporary artifact").unlink(missing_ok=True)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _reset_fields(state: dict, resume_status: str) -> dict:
    for key in STALE_KEYS:
        state.pop(key, None)
    state.pop("interrupted_status", None)
    if resume_status == "IMPLEMENTING":
        state["attempt"] = max(int(state.get("attempt", 0)) - 1, 0)
    return {}
