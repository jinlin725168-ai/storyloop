"""Link an owned feature collision to a human base draft and preserve its reference."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .base_upgrade import draft_from_proposal
from .baseline import _git
from .changes import _save, load_change
from .models import StoryLoopError, require
from .path_security import repository_relative
from .processutil import redact
from .project_boundaries import _changed_paths, base_scope_path_allowed
from .project_loop_support import save_run
from .storage import load_json, write_json

MAX_REFERENCE_FILE = 200_000
MAX_REFERENCE = 500_000


def link_base_collision(project: Path, state: dict, boundary: dict,
                        touched: list, preexisting: set[str]) -> str | None:
    """Only the current writer's snapshot delta may be archived and restored."""
    violations = boundary["violations"]
    if (boundary["status"] != "BASE_CHANGE_REQUIRED"
            or boundary.get("ci_requires_human_proposal")
            or not all(base_scope_path_allowed("foundation", item["path"])
                       for item in violations)):
        return None
    owned = {item.path for item in touched if not item.path.endswith("/")}
    visible = {item["path"] for item in boundary["allowed"] + violations}
    if preexisting or not owned or owned != visible or owned != set(_changed_paths(project)):
        raise StoryLoopError("Cannot attribute all candidate paths to this implementation call",
                             code="WORKSPACE_TAINTED")
    entries = [_reference_entry(project, item.path, item.kind,
                                next(record["category"] for record in boundary["allowed"] + violations
                                     if record["path"] == item.path))
               for item in touched if not item.path.endswith("/")]
    proposal_path = Path(boundary["base_proposal"])
    base_id = proposal_path.stem
    _store_reference(project, proposal_path, state, entries)
    draft_from_proposal(project, base_id, scope="foundation")
    change = load_change(project, state["change_id"])
    require(change.get("blocked_by", base_id) == base_id, "Feature has another blocking base")
    change["blocked_by"] = base_id
    _save(project, change)
    state["blocked_by"] = base_id
    save_run(project, state)
    for entry in entries:
        _restore_candidate(project, entry)
    require(not _changed_paths(project), "Candidate cleanup left Git-visible changes")
    return base_id


def _reference_entry(project: Path, relative: str, kind: str, category: str) -> dict:
    relative = repository_relative(relative, "candidate path")
    path = project / relative
    cursor = project
    for part in Path(relative).parts:
        cursor /= part
        if cursor.is_symlink() or (hasattr(cursor, "is_junction") and cursor.is_junction()):
            raise StoryLoopError("Candidate path is a link", code="WORKSPACE_TAINTED")
    if path.exists() and (not path.is_file() or path.stat().st_size > MAX_REFERENCE_FILE):
        raise StoryLoopError("Candidate reference is not a bounded regular file",
                             code="WORKSPACE_TAINTED")
    after = path.read_bytes() if path.exists() else b""
    tracked = relative in _git(project, "ls-files", "--", relative).splitlines()
    before = _git(project, "show", f"HEAD:{relative}").encode("utf-8") if tracked else b""
    if len(before) > MAX_REFERENCE_FILE or len(after) > MAX_REFERENCE_FILE:
        raise StoryLoopError("Candidate reference exceeds size limit", code="WORKSPACE_TAINTED")
    try:
        old, new = before.decode("utf-8"), after.decode("utf-8")
    except UnicodeError as exc:
        raise StoryLoopError("Candidate reference is not UTF-8 text",
                             code="WORKSPACE_TAINTED") from exc
    return {"path": relative, "kind": kind, "category": category, "tracked": tracked,
            "before_sha256": hashlib.sha256(before).hexdigest(),
            "after_sha256": hashlib.sha256(after).hexdigest(),
            "before": redact(old), "after": redact(new)}


def _store_reference(project: Path, proposal_path: Path, state: dict, entries: list[dict]) -> None:
    base_id = proposal_path.stem
    reference_path = proposal_path.with_name(base_id + "-reference.json")
    relative = reference_path.relative_to(project).as_posix()
    record = {"version": 1, "source_change": state["change_id"],
              "source_commit": _git(project, "rev-parse", "HEAD").strip(),
              "changes": entries, "note": "仅供参考；不进入 base 合同或候选实现"}
    if reference_path.exists():
        require(load_json(reference_path) == record, "Base proposal reference changed after creation")
    else:
        write_json(reference_path, record, max_bytes=MAX_REFERENCE)
    digest = hashlib.sha256(reference_path.read_bytes()).hexdigest()
    proposal = load_json(proposal_path)
    expected = {"path": relative, "sha256": digest}
    require(proposal.get("reference", expected) == expected,
            "Base proposal reference changed after creation")
    proposal["reference"] = expected
    write_json(proposal_path, proposal, max_bytes=1_000_000)


def _restore_candidate(project: Path, entry: dict) -> None:
    path = project / entry["path"]
    if entry["tracked"]:
        _git(project, "restore", "--source=HEAD", "--worktree", "--", entry["path"])
    elif path.is_file():
        path.unlink()
