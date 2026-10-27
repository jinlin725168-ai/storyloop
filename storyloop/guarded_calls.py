"""Business-level errata timing around pure workspace guards."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from .errata import defer_errata
from .project_loop_guards import read_only_call as _read_only_call, write_safely as _write_safely


@defer_errata()
def write_safely(project: Path, change_id: str, operation: Callable[[], dict],
                 post_call_guard: Callable[[], None] | None = None,
                 excluded_control_paths: frozenset[str] = frozenset(),
                 exclude_root_git_snapshot: bool = False,
                 allowed_git_worktree_attempt: str | None = None) -> tuple[dict, list]:
    return _write_safely(project, change_id, operation, post_call_guard,
                         excluded_control_paths, exclude_root_git_snapshot,
                         allowed_git_worktree_attempt)


@defer_errata()
def read_only_call(project: Path, change_id: str, operation: Callable[[], object],
                   allowed_evidence: str | None = None,
                   post_call_guard: Callable[[], None] | None = None) -> object:
    return _read_only_call(project, change_id, operation, allowed_evidence, post_call_guard)
