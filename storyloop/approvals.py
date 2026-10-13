"""User approval and approved OpenSpec materialization."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .changes import (contract_digest, draft_directory, load_change,
                      write_change_preview)
from .models import require, text
from .path_security import safe_target
from .requirement_checklist import blocking_items, evaluate_checklist
from .storage import write_bytes_atomic, write_json

Clock = Callable[[], datetime]


def approve_change(
    project: Path, change_id: str, approved_by: str = "user",
    now: Clock | None = None,
) -> dict:
    """Lock the current product-only revision after explicit user approval."""
    project = Path(project).resolve()
    state = load_change(project, change_id)
    product = state["product"]
    require(not product["questions"], "Resolve all product questions before approval")
    require(bool(product["stories"]), "Approval requires at least one user story")
    blocking = blocking_items(evaluate_checklist(product, project))
    require(not blocking, "Requirement checklist has blocking items; revise the contract: "
            + "; ".join(item["text"] for item in blocking[:3]))
    approved_by = text(approved_by, "approver")
    digest = contract_digest(product)
    if state["status"] == "APPROVED":
        require(state["approval"]["contract_digest"] == digest,
                "Approved product contract is stale")
        return deepcopy(state)
    require(state["status"] == "DRAFT", "Only a draft can be approved")
    moment = (now or _utc_now)()
    state["status"] = "APPROVED"
    state["approval"] = {
        "revision": state["revision"],
        "contract_digest": digest,
        "approved_by": approved_by,
        "approved_at": _timestamp(moment),
    }
    state["updated_at"] = _timestamp(moment)
    _write_state(project, state)
    return deepcopy(state)


def materialize_approved_change(project: Path, change_id: str) -> Path:
    """Copy the locked draft into the repository's tracked OpenSpec area."""
    project = Path(project).resolve()
    state = load_change(project, change_id)
    require(state["status"] in {"APPROVED", "RUNNING", "PASSED"} and state["approval"],
            "Change must be approved before materialization")
    require(state["approval"]["contract_digest"] == contract_digest(state["product"]),
            "Approved product contract is stale")
    write_change_preview(project, state)
    source = safe_target(project, f".storyloop/drafts/{change_id}", "draft path",
                         target_directory=True)
    destination = safe_target(project, f"openspec/changes/{change_id}", "OpenSpec path",
                              target_directory=True)
    require(source.is_dir(), "Approved draft files are missing")
    destination.mkdir(parents=True, exist_ok=True)
    for name in ("proposal.md", "stories.json", "acceptance.json", "traceability.json", "checklist.md"):
        source_file = source / name
        target = destination / name
        require(source_file.is_file() and not source_file.is_symlink(),
                "Approved draft contains an unsafe or missing file")
        content = source_file.read_bytes()
        if target.exists():
            require(target.is_file() and not target.is_symlink() and target.read_bytes() == content,
                    "Existing OpenSpec contract differs from the approval")
        else:
            _write_bytes(target, content)
    for source_file in sorted((source / "specs").rglob("spec.md")) if (source / "specs").is_dir() else []:
        relative = source_file.relative_to(source)
        target = destination / relative
        content = source_file.read_bytes()
        if target.exists():
            require(target.is_file() and target.read_bytes() == content,
                    "Existing OpenSpec delta spec differs from the approval")
        else:
            _write_bytes(target, content)
    write_json(destination / "approval.json", deepcopy(state["approval"]), max_bytes=64_000)
    return destination


def _write_state(project: Path, state: dict) -> None:
    path = project / ".storyloop" / "state" / "changes" / f"{state['change_id']}.json"
    write_json(path, state, max_bytes=2_000_000)


def _write_bytes(path: Path, content: bytes) -> None:
    write_bytes_atomic(path, content)


def _timestamp(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
