"""Persistence and integrity helpers for the external project loop."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from .baseline import _git
from .smoke import RED_EVIDENCE, RESULT_EVIDENCE
from .run_state import run_path, save_run  # noqa: F401  # 兼容再导出：阶段 D 拆分后保留旧入口


def control_digest(
    project: Path, change_id: str, excluded_evidence: str | None = None,
    excluded_paths: frozenset[str] = frozenset(),
    allowed_git_worktree_attempt: str | None = None,
) -> str:
    # .git/index is deliberately absent: read-only git commands rewrite its stat cache.
    # Staged content is covered semantically by `git diff --cached` below.
    relative_paths = [
        ".git/HEAD", ".git/config", ".git/info/exclude", ".git/packed-refs",
        ".storyloop/config.json", ".storyloop/baseline.json", "openspec/principles.json",
        ".storyloop/state/readiness.json",
        f"openspec/changes/{change_id}/routing-plan.json",
        f".storyloop/state/changes/{change_id}.json",
        f".storyloop/state/smoke/{change_id}.json",
        f".storyloop/state/suites/{change_id}.json",
        f".storyloop/state/runs/{change_id}.json",
    ]
    for name in (RED_EVIDENCE, RESULT_EVIDENCE):
        if name != excluded_evidence:
            relative_paths.append(f".storyloop/evidence/{change_id}/{name}")
    relative_paths = [path for path in relative_paths if path not in excluded_paths]
    if allowed_git_worktree_attempt is not None:
        relative_paths.extend(_git_metadata_paths(project, allowed_git_worktree_attempt))
    digest = hashlib.sha256(_git(project, "rev-parse", "HEAD").encode("utf-8"))
    digest.update(_git(project, "branch", "--show-current").encode("utf-8"))
    digest.update(_git(
        project, "for-each-ref", "--format=%(refname):%(objectname)"
    ).encode("utf-8"))
    digest.update(_git(project, "diff", "--cached", "--name-status").encode("utf-8"))
    digest.update(_path_digest(project, relative_paths).encode("ascii"))
    return digest.hexdigest()


def _git_metadata_paths(project: Path, attempt: str) -> list[str]:
    """Hash shared Git metadata; only this detached checkout and objects may change."""
    root = project / ".git"
    if not root.is_dir():
        return []
    paths: list[str] = []
    for directory, names, files in os.walk(root, followlinks=False):
        current = Path(directory)
        parent = current.relative_to(project).as_posix()
        names[:] = sorted(name for name in names
                          if not (parent == ".git" and name == "objects")
                          and not (parent == ".git/worktrees" and name == attempt))
        for name in [*names, *sorted(files)]:
            relative = f"{parent}/{name}"
            if relative != ".git/index":
                paths.append(relative)
    return paths


def relative_files(project: Path, roots: list[Path]) -> list[str]:
    return sorted(path.relative_to(project).as_posix() for root in roots
                  for path in root.rglob("*") if path.is_file() and not path.is_symlink())


def _path_digest(project: Path, relative_paths: list[str]) -> str:
    digest = hashlib.sha256()
    for relative in sorted(relative_paths, key=str.casefold):
        path = project / relative
        digest.update(relative.encode("utf-8") + b"\0")
        if path.is_file() and not path.is_symlink():
            digest.update(hashlib.sha256(path.read_bytes()).digest())
        else:
            digest.update(b"<missing-or-unsafe>")
    return digest.hexdigest()
