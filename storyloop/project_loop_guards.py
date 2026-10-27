"""Guards around every agent call of the external loop: what a writer or a reader may change.

A writing call (implement, characterize) may change the workspace but never the control plane or
the Git index; a read-only call (acceptance, design, review, RED) may change nothing but declared
scratch locations and its one allowed evidence file. Violations raise WORKSPACE_TAINTED.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from .baseline import _git, load_project_config
from .boundaries import capture_snapshot, snapshot_changes
from .models import StoryLoopError, require
from .project_loop_support import control_digest


_VISIBILITY_MESSAGE_LIMIT = 20


def write_safely(project: Path, change_id: str, operation: Callable[[], dict],
                 post_call_guard: Callable[[], None] | None = None,
                 excluded_control_paths: frozenset[str] = frozenset(),
                 exclude_git_metadata: bool = False) -> tuple[dict, list]:
    """Run an agent that may write the workspace; the control plane and the index must stay untouched."""
    before = capture_snapshot(project, include_control=True, include_ignored=True,
                              exclude_git=exclude_git_metadata)
    control = control_digest(project, change_id, excluded_paths=excluded_control_paths,
                             exclude_git_metadata=exclude_git_metadata)
    staged = _staged_index(project)
    failure: BaseException | None = None
    try:
        response = operation()
    except BaseException as exc:
        failure = exc
    finally:
        if post_call_guard is not None:
            post_call_guard()
        require_control_unchanged(
            control_digest(project, change_id, excluded_paths=excluded_control_paths,
                           exclude_git_metadata=exclude_git_metadata) == control,
            "Implementation agent modified the protected control plane",
        )
        require_control_unchanged(_staged_index(project) == staged,
                                  "Implementation agent staged Git changes")
    after = capture_snapshot(project, include_control=True, include_ignored=True,
                             exclude_git=exclude_git_metadata)
    changes = snapshot_changes(before, after)
    _require_drafts_unchanged(changes)
    changes = _without_scratch(changes, scratch_paths(project))
    if failure is not None:
        if changes:
            raise StoryLoopError(
                "Implementation failed after modifying the workspace",
                code="WORKSPACE_TAINTED",
            ) from failure
        raise failure
    return response, changes


def read_only_call(
    project: Path, change_id: str, operation: Callable[[], object],
    allowed_evidence: str | None = None,
    post_call_guard: Callable[[], None] | None = None,
) -> object:
    before = capture_snapshot(project, include_control=True, include_ignored=True)
    control = control_digest(project, change_id, excluded_evidence=allowed_evidence)
    staged = _staged_index(project)
    try:
        return operation()
    finally:
        if post_call_guard is not None:
            post_call_guard()
        allowed = (f".storyloop/evidence/{change_id}/{allowed_evidence}"
                   if allowed_evidence else None)
        verify_read_only_snapshot(
            before, capture_snapshot(project, include_control=True, include_ignored=True), allowed,
            scratch_paths(project),
        )
        require_control_unchanged(_staged_index(project) == staged,
                                  "Read-only operation staged Git changes")
        require_control_unchanged(
            control_digest(project, change_id,
                           excluded_evidence=allowed_evidence) == control,
            "Read-only operation modified Git or the protected control plane",
        )


def require_control_unchanged(condition: bool, message: str) -> None:
    if not condition:
        raise StoryLoopError(message, code="WORKSPACE_TAINTED")


def verify_read_only_snapshot(before, after, allowed_path: str | None,
                              scratch: list[str] | None = None) -> None:
    changes = snapshot_changes(before, after)
    _require_drafts_unchanged(changes)
    changes = _without_scratch(changes, scratch or [])
    unexpected = [item for item in changes
                  if item.path != allowed_path
                  and not _allowed_parent_directory(item.path, allowed_path)]
    if unexpected:
        paths = ", ".join(item.path for item in unexpected[:20])
        raise StoryLoopError(f"Read-only operation modified the project: {paths}",
                             code="WORKSPACE_TAINTED")


def _allowed_parent_directory(path: str, allowed_path: str | None) -> bool:
    return bool(path.endswith("/") and allowed_path
                and allowed_path.startswith(path))


def scratch_paths(project: Path) -> list[str]:
    """Ignored locations (names or relative paths) agents may write, e.g. build output."""
    profile = load_project_config(project).get("profile", {})
    values = profile.get("scratch_paths", []) if isinstance(profile, dict) else []
    require(isinstance(values, list) and all(isinstance(item, str) and item for item in values),
            "scratch_paths must be a list of names or relative paths")
    return values


def _staged_index(project: Path) -> str:
    return _git(project, "diff", "--cached", "--name-status")


def _is_scratch(path: str, scratch: list[str]) -> bool:
    """Tolerated: .git/index refreshes, declared scratch names anywhere, declared relative paths."""
    normalized = path.rstrip("/")
    if normalized == ".git/index":
        return True
    # The engine's own call ledger is written while agents run; it is bookkeeping, not a change.
    if normalized == ".storyloop/state/telemetry" or normalized.startswith(".storyloop/state/telemetry/"):
        return True
    if normalized == ".storyloop" or normalized.startswith(".storyloop/"):
        return False
    parts = normalized.split("/")
    return any(part == item for part in parts for item in scratch) or any(
        normalized == item.rstrip("/") or normalized.startswith(item.rstrip("/") + "/")
        for item in scratch if "/" in item)


def _without_scratch(changes: list, scratch: list[str]) -> list:
    """Drop tolerated file changes and directory entries whose only changes are tolerated."""
    files = [item for item in changes if not item.path.endswith("/")]
    kept = [item for item in files if not _is_scratch(item.path, scratch)]
    dropped = [item for item in files if _is_scratch(item.path, scratch)]
    directories = [item for item in changes if item.path.endswith("/")]
    result = list(kept)
    for entry in directories:
        if _is_scratch(entry.path, scratch):
            continue
        prefix = "" if entry.path == "./" else entry.path
        under_kept = any(item.path.startswith(prefix) for item in kept)
        under_dropped = any(item.path.startswith(prefix) for item in dropped) or any(
            other.path.startswith(prefix) and _is_scratch(other.path, scratch)
            for other in directories)
        if under_kept or not under_dropped:
            result.append(entry)
    return result


def require_git_visible(touched: list, boundary: dict) -> None:
    visible = {item["path"] for key in ("allowed", "violations")
               for item in boundary[key]}
    invisible = [item for item in touched
                 if not item.path.endswith("/") and item.path not in visible]
    if invisible:
        shown = [item.path for item in invisible[:_VISIBILITY_MESSAGE_LIMIT]]
        suffix = (f" (+{len(invisible) - len(shown)} more)"
                  if len(invisible) > len(shown) else "")
        raise StoryLoopError(
            "Implementation created or modified ignored files: " + ", ".join(shown) + suffix,
            code="WORKSPACE_TAINTED",
            details={"invisible": [
                {"path": item.path, "kind": item.kind}
                for item in invisible
            ]},
        )


def exists_at(project: Path, commit: str, path: str) -> bool:
    try:
        _git(project, "cat-file", "-e", f"{commit}:{path}")
        return True
    except Exception:  # noqa: BLE001  (git reports a missing object through a non-zero exit)
        return False


def _require_drafts_unchanged(changes):
    # Engine drafts are protected even if a generic scratch name also matches them.
    require_control_unchanged(
        not any(item.path.rstrip('/') in {'.storyloop/drafts', '.storyloop/drafts.lock'}
                or item.path.startswith('.storyloop/drafts/') for item in changes),
        'Agent modified protected drafts',
    )
