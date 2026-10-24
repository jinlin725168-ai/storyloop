"""Void a stopped run and reopen its revised contract under a new change id."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Callable

from .changes import _save, contract_digest, create_change, load_change, write_change_preview
from .run_transitions import transition, set_change_status, SUPERSEDABLE
from .models import identifier, require, text
from .guarded_calls import read_only_call
from .project_loop_support import run_path, save_run
from .stop_report import stop_of
from .storage import load_json, locked




def supersede_change(project: Path, old_id: str, message: str,
                     reviser: Callable[[dict], dict], reason: str | None = None) -> dict:
    """Revise a stopped contract, preserving both histories and their branches."""
    project = Path(project).resolve()
    old_id = identifier(old_id, "change id")
    message = text(message, "revision message")
    with locked(project):
        return _supersede_locked(project, old_id, message, reviser, reason)


def _supersede_locked(project: Path, old_id: str, message: str,
                      reviser: Callable[[dict], dict], reason: str | None) -> dict:
    old = load_change(project, old_id)
    path = run_path(project, old_id)
    require(path.exists(), "Only a started run can be superseded; before that, revise the draft")
    run = load_json(path)
    _validate_run(old, run)
    status = run.get("status")
    if status == "SUPERSEDED":
        checked_reason = _retry_reason(reason)
    else:
        require(status in SUPERSEDABLE,
                f"A run in {status} cannot be superseded; a merged change is revised by a new change")
        checked_reason = _reason(status, reason)
    existing = _existing_result(project, old, message, checked_reason, path)
    if existing is not None:
        return existing
    link, request = _revision_context(project, old, run, message, checked_reason)
    product = read_only_call(project, old_id, lambda: reviser(request))
    new = create_change(project, old["idea"], product, supersedes=link)
    _finish_link(project, old, run, new["change_id"])
    return _result(project, new, run)


def _reason(status: str, reason: str | None) -> str | None:
    if status == "NEEDS_SPEC_FIX":
        return text(reason, "supersede reason") if reason is not None else None
    require(reason is not None,
            "Superseding outside NEEDS_SPEC_FIX needs --reason: why is the contract wrong")
    return text(reason, "supersede reason")


def _retry_reason(reason: str | None) -> str | None:
    return text(reason, "supersede reason") if reason is not None else None


def _validate_run(old: dict, run: dict) -> None:
    approval = old.get("approval") or {}
    require(run.get("change_id") == old["change_id"], "Run state identity mismatch")
    require(approval and approval.get("contract_digest") == contract_digest(old["product"])
            and run.get("contract_digest") == approval.get("contract_digest"),
            "Run state does not match the approved contract")


def _revision_context(project: Path, old: dict, run: dict, message: str,
                      reason: str | None) -> tuple[dict, dict]:
    stop = stop_of(project, run) or {}
    plan = run.get("design_plan") or {}
    link = {
        "change_id": old["change_id"], "message": message, "reason": reason,
        "blocker": stop.get("blocker"), "source_branch": run["source_branch"],
        "design_summary": plan.get("summary"),
    }
    context = {**link, "facts": deepcopy(run.get("facts") or []),
               "evidence": deepcopy(stop.get("evidence") or [])}
    request = {"idea": old["idea"], "message": message,
               "current_product": deepcopy(old["product"]), "supersede": context}
    return link, request


def _finish_link(project: Path, old: dict, run: dict, new_id: str) -> None:
    updated = set_change_status(project, {**old, "superseded_by": new_id}, "SUPERSEDED")
    write_change_preview(project, updated)
    updated_run = dict(run)
    transition(project, updated_run, "SUPERSEDED", event="supersede", updates={"superseded_by": new_id})
    save_run(project, updated_run)


def _existing_result(project: Path, old: dict, message: str, reason: str | None,
                     path: Path) -> dict | None:
    new = _linked_child(project, old, message, reason)
    if new is None:
        return None
    require(path.exists(), "Superseded change is missing its run state")
    run = load_json(path)
    _validate_run(old, run)
    new_id = new["change_id"]
    _save(project, new)
    if old.get("status") != "SUPERSEDED" or old.get("superseded_by") != new_id \
            or run.get("status") != "SUPERSEDED" or run.get("superseded_by") != new_id:
        require(run.get("status") in SUPERSEDABLE or run.get("status") == "SUPERSEDED",
                "Superseded change and run state are inconsistent")
        _finish_link(project, old, run, new_id)
        run = dict(run)
        transition(project, run, "SUPERSEDED", event="supersede_result", updates={"superseded_by": new_id})
    return _result(project, new, run)


def _linked_child(project: Path, old: dict, message: str, reason: str | None) -> dict | None:
    expected = old.get("superseded_by")
    root = project / ".storyloop/state/changes"
    matches = []
    for candidate in root.glob("*.json") if root.is_dir() else ():
        state = load_change(project, candidate.stem)
        link = state.get("supersedes") or {}
        if link.get("change_id") == old["change_id"]:
            matches.append(state)
    require(len(matches) <= 1, "A change has multiple superseding drafts")
    if not matches:
        require(not expected, "Superseding change link is missing")
        return None
    match = matches[0]
    require((not expected or expected == match["change_id"]) and
            match["supersedes"]["message"] == message and
            match["supersedes"]["reason"] == reason,
            "Change was already superseded with a different revision")
    return match


def _result(project: Path, new: dict, old_run: dict) -> dict:
    from .run_hints import draft_next, next_step
    result = deepcopy(new)
    result["next"] = (draft_next(project, new) if new['status'] == 'DRAFT'
                      else next_step(new['status'], new['change_id'], project))
    return result
