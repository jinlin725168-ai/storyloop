"""Approval and product-facing lifecycle operations."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from .evidence import valid_evidence, sanitized
from .models import StoryLoopError, require, validate_product, validate_checks, validate_config
from .storage import load_json, write_json, approval_digest, workspace_digest, locked

SUCCESS = {"PASSED", "AWAITING_ACCEPTANCE", "ACCEPTED"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def inputs(project: Path) -> tuple[dict, dict, dict]:
    product = validate_product(load_json(project / "product.json"))
    checks = validate_checks(load_json(project / "checks.json"), product)
    config = validate_config(load_json(project / "config.json"))
    return product, checks, config


def approve(project: Path) -> dict:
    """Explicitly approve current rules AND trusted executable checks/configuration."""
    project = Path(project).resolve()
    with locked(project):
        product, _, _ = inputs(project)
        require(bool(product["stories"]), "At least one story is required")
        require(not product["questions"], "Resolve product questions before approval")
        fingerprint = approval_digest(project)
        approval = {"version": 1, "digest": fingerprint, "approved_at": now()}
        previous = project / ".storyloop" / "approval.json"
        if previous.exists() and load_json(previous).get("digest") == fingerprint:
            return load_json(previous)
        if previous.exists():
            history = project / ".storyloop" / "history" / (now().replace(":", "-") + ".json")
            write_json(history, load_json(project / ".storyloop" / "state.json"))
        write_json(previous, approval)
        write_json(project / ".storyloop" / "state.json", {"version": 1, "approval_digest": fingerprint,
                   "stories": {s["id"]: {"status": "PENDING", "attempts": 0, "elapsed_seconds": 0,
                              "evidence": []} for s in product["stories"]}})
        return approval


def assert_approved(project: Path) -> str:
    path = project / ".storyloop" / "approval.json"
    require(path.exists(), "Review the story and test sources, then run approve before execution")
    digest = approval_digest(project)
    require(load_json(path).get("digest") == digest, "Approved inputs changed; review and approve a new baseline")
    return digest


def status(project: Path) -> dict:
    """Report evidence freshness without silently accepting changed code."""
    project = Path(project).resolve()
    state = deepcopy(load_json(project / ".storyloop" / "state.json"))
    digest, source = approval_digest(project), workspace_digest(project)
    approval = project / ".storyloop/approval.json"
    approved = approval.exists() and load_json(approval).get("digest") == digest
    for record in state["stories"].values():
        if record.get("status") in SUCCESS:
            if not approved or record.get("approval_digest") != digest or record.get("workspace_digest") != source or not valid_evidence(project, record):
                record.update(status="STALE", reason="Source, approval or evidence changed/missing")
    statuses = {record["status"] for record in state["stories"].values()}
    state["status"] = _overall(statuses)
    return state


def _overall(statuses: set) -> str:
    for label in ("BLOCKED", "NEEDS_INPUT", "FAILED", "STALE", "RUNNING", "INTERRUPTED", "PENDING", "AWAITING_ACCEPTANCE"):
        if label in statuses:
            return label
    return "ACCEPTED" if statuses == {"ACCEPTED"} else "PASSED" if statuses else "DRAFT"


def accept(project: Path, story_id: str, note: str) -> dict:
    """Record human acceptance only against current, verified implementation."""
    project = Path(project).resolve()
    require(isinstance(note, str) and bool(note.strip()), "Human acceptance needs a meaningful note")
    with locked(project):
        assert_approved(project)
        view = status(project)
        require(story_id in view["stories"], "Unknown story")
        require(view["stories"][story_id]["status"] in SUCCESS, "Story needs fresh passing evidence before acceptance")
        state = load_json(project / ".storyloop" / "state.json")
        state["stories"][story_id].update(status="ACCEPTED", accepted_note=sanitized(note.strip()), accepted_at=now())
        write_json(project / ".storyloop" / "state.json", state)
    return status(project)


def run(project: Path, story_id: str | None = None) -> dict:
    """Execute or resume scoped stories, including their prior regressions."""
    from .runner import Runner
    project = Path(project).resolve()
    with locked(project):
        digest = assert_approved(project)
        product, checks, config = inputs(project)
        require(not product["questions"], "Unresolved product questions")
        runner = Runner(project, product, checks["checks"], config, digest)
        runner.run(story_id)
    return status(project)
