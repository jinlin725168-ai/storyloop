"""Unified, safely addressed lesson and errata drafts."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re

from .models import StoryLoopError, require
from .path_security import safe_target
from .storage import load_json, locked_file, write_json, write_text_atomic

DRAFT_KINDS = ("lesson", "errata")
_REF = re.compile(r"^(lesson|errata)/(D-[A-Za-z0-9-]+)$")
_CHANGE_SOURCE = re.compile(r"^(?:FEAT|FIX|REF)-[A-Za-z0-9-]+$")
_STATUSES = {"pending", "approved", "rejected"}


def create_draft(
    project: Path, kind: str, source: str, proposer: str | None,
    payload: dict, body: str | None,
) -> str:
    """Create a pending draft and return its identifier."""
    require(kind in DRAFT_KINDS, "Invalid draft kind")
    require(isinstance(source, str) and bool(source.strip()), "Draft source is required")
    require(proposer is None or isinstance(proposer, str), "Invalid draft proposer")
    require(isinstance(payload, dict), "Draft payload must be an object")
    require(body is None or isinstance(body, str), "Draft body must be text")
    project = Path(project).resolve()
    lock = safe_target(project, ".storyloop/drafts.lock", "draft lock")
    with locked_file(lock):
        moment = datetime.now(timezone.utc)
        id_source = source if _CHANGE_SOURCE.fullmatch(source) else _manual_source(moment)
        draft_id = _next_id(project, id_source)
        directory = safe_target(
            project, f".storyloop/drafts/{kind}/{draft_id}", "draft directory",
            target_directory=True,
        )
        require(not directory.exists(), "Draft already exists")
        created_at = moment.isoformat(timespec="seconds")
        write_json(directory / "draft.json", {
            "kind": kind, "created_at": created_at, "source": source,
            "proposer": proposer, "payload": payload, "status": "pending",
            "failure": None,
        })
        if body is not None:
            write_text_atomic(directory / "body.md", body)
    return draft_id


def load_draft(project: Path, ref: str) -> dict:
    """Load one lesson or errata draft from a validated KIND/ID reference."""
    kind, draft_id, directory = _draft_target(project, ref)
    metadata = load_json(safe_target(directory, "draft.json", "draft metadata"))
    require(metadata.get("kind") == kind, "Draft kind does not match its path")
    body_path = safe_target(directory, "body.md", "draft body")
    body = body_path.read_text(encoding="utf-8") if body_path.is_file() else None
    return {**metadata, "id": draft_id, "kind": kind, "body": body}


def set_draft_status(
    project: Path, ref: str, status: str, failure: str | None = None,
) -> None:
    """Update draft workflow metadata while preserving an edited body verbatim."""
    require(status in _STATUSES, "Invalid draft status")
    require(failure is None or isinstance(failure, str), "Invalid draft failure")
    project = Path(project).resolve()
    lock = safe_target(project, ".storyloop/drafts.lock", "draft lock")
    with locked_file(lock):
        kind, _, directory = _draft_target(project, ref)
        path = safe_target(directory, "draft.json", "draft metadata")
        metadata = load_json(path)
        require(metadata.get("kind") == kind, "Draft kind does not match its path")
        metadata["status"] = status
        metadata["failure"] = failure
        write_json(path, metadata)


def list_drafts(project: Path) -> list[dict]:
    """List pending memory drafts plus the existing change and smoke drafts."""
    project = Path(project).resolve()
    root = safe_target(project, ".storyloop/drafts", "drafts directory", target_directory=True)
    if not root.is_dir():
        return []
    items = _memory_drafts(project, root)
    items.extend(_legacy_drafts(root, "smoke", "冒烟草案"))
    excluded = {*DRAFT_KINDS, "smoke"}
    for directory in root.iterdir():
        if directory.is_dir() and directory.name not in excluded:
            items.append(_legacy_item(directory, "change", "变更草案"))
    return sorted(items, key=lambda item: (item["created_at"], item["kind"], item["id"]))


def reject_draft(project: Path, ref: str, reason: str) -> dict:
    """Retain a rejected draft with its human reason."""
    require(isinstance(reason, str) and bool(reason.strip()), "Rejecting a draft needs --reason")
    set_draft_status(project, ref, "rejected", reason)
    return load_draft(project, ref)


def _draft_target(project: Path, ref: str) -> tuple[str, str, Path]:
    require(isinstance(ref, str), "Invalid draft reference")
    match = _REF.fullmatch(ref)
    require(match is not None, "Invalid draft reference")
    kind, draft_id = match.groups()
    directory = safe_target(
        Path(project).resolve(), f".storyloop/drafts/{kind}/{draft_id}",
        "draft reference", target_directory=True,
    )
    require(directory.is_dir(), "Draft does not exist")
    return kind, draft_id, directory


def _next_id(project: Path, id_source: str) -> str:
    prefix = f"D-{id_source}-"
    highest = 0
    for kind in DRAFT_KINDS:
        root = safe_target(project, f".storyloop/drafts/{kind}", "draft kind", target_directory=True)
        if not root.is_dir():
            continue
        for path in root.iterdir():
            suffix = path.name.removeprefix(prefix)
            if path.name.startswith(prefix) and suffix.isdigit():
                highest = max(highest, int(suffix))
    require(highest < 99, "Draft source sequence capacity exhausted")
    return prefix + f"{highest + 1:02d}"


def _manual_source(moment: datetime) -> str:
    return "manual-" + moment.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S")


def _memory_drafts(project: Path, root: Path) -> list[dict]:
    items = []
    for kind in DRAFT_KINDS:
        kind_root = safe_target(root, kind, "draft kind", target_directory=True)
        if not kind_root.is_dir():
            continue
        for directory in kind_root.iterdir():
            if directory.is_dir():
                try:
                    draft = load_draft(project, f"{kind}/{directory.name}")
                    require(isinstance(draft.get("status"), str) and draft["status"] in _STATUSES
                            and isinstance(draft.get("created_at"), str), "Invalid draft metadata")
                except (StoryLoopError, OSError, UnicodeError):
                    items.append({"id": directory.name, "kind": kind, "created_at": "",
                                  "status": "invalid", "failure": "DRAFT_INVALID"})
                    continue
                if draft.get("status") == "pending":
                    items.append(draft)
    return items


def _legacy_drafts(root: Path, kind: str, summary: str) -> list[dict]:
    parent = safe_target(root, kind, "legacy draft kind", target_directory=True)
    if not parent.is_dir():
        return []
    return [_legacy_item(path, kind, summary) for path in parent.iterdir() if path.is_dir()]


def _legacy_item(directory: Path, kind: str, summary: str) -> dict:
    created_at = datetime.fromtimestamp(directory.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")
    return {"id": directory.name, "kind": kind, "summary": summary, "created_at": created_at}
