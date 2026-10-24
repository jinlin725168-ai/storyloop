"""Safe local Git lifecycle for approved product changes."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
import re

from .git_command import git as _git
from .models import StoryLoopError, identifier, require, text
from .path_security import repository_relative

FEATURE = re.compile(r"feature/[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
BASE_BRANCH = re.compile(r"storyloop/BASE-[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}")


def start_feature(project: Path, change_id: str) -> dict:
    """Create a local feature branch from the current clean named branch."""
    project = Path(project).resolve()
    change_id = identifier(change_id, "change id")
    _require_clean(project)
    source = _git(project, "branch", "--show-current").strip()
    require(_valid_branch(source), "Git repository must be on a safe named branch")
    branch = f"feature/{change_id}"
    require(FEATURE.fullmatch(branch) is not None, "Invalid feature branch")
    existing = _git(project, "branch", "--list", branch).strip()
    require(not existing, "Feature branch already exists; resume it explicitly")
    _git(project, "switch", "-c", branch)
    return {"status": "STARTED", "branch": branch, "source_branch": source,
            "source_head": _git(project, "rev-parse", "HEAD").strip()}


def start_base(project: Path, change_id: str,
               allowed_dirty_paths: set[str] | None = None) -> dict:
    """Create the local branch on which a human commits one approved base change."""
    project = Path(project).resolve()
    change_id = identifier(change_id, "change id")
    if not allowed_dirty_paths:
        _require_clean(project)
    else:
        records = [item for item in _git(project, "status", "--porcelain=v1",
                                         "--untracked-files=all", "-z").split("\0") if item]
        require(all(item[:2] in {" M", " D", "??"}
                    and item[3:].replace("\\", "/") in allowed_dirty_paths for item in records),
                "Base branch has changes outside the approved paths")
    source = _git(project, "branch", "--show-current").strip()
    require(_valid_branch(source), "Git repository must be on a safe named branch")
    branch = f"storyloop/{change_id}"
    require(BASE_BRANCH.fullmatch(branch) is not None, "Invalid base branch")
    require(not _git(project, "branch", "--list", branch).strip(),
            "Base branch already exists; resume it explicitly")
    _git(project, "switch", "-c", branch)
    return {"status": "STARTED", "branch": branch, "source_branch": source,
            "source_head": _git(project, "rev-parse", "HEAD").strip()}


def commit_paths(project: Path, paths: list[str], message: str, *, details: bool = True) -> dict:
    """Stage only explicit safe paths and create a hook-free local commit."""
    project = Path(project).resolve()
    require(isinstance(paths, list) and bool(paths), "Commit needs explicit paths")
    normalized = [_safe_path(item) for item in paths]
    require(len(set(normalized)) == len(normalized), "Duplicate commit path")
    message = text(message, "commit message")
    branch = _git(project, "branch", "--show-current").strip() if details else None
    _git(project, "add", "--", *normalized)
    status = _git(project, "status", "--porcelain=v1", "--untracked-files=all")
    require(bool(status.strip()), "Commit has no changes")
    try:
        # Commits carry the repository's own configured identity: the loop acts for its user.
        _git(project, "commit", "--no-verify", "-m", message)
    except StoryLoopError as exc:
        raise StoryLoopError(f"Local Git commit failed: {exc}", code="GIT_BLOCKED") from exc
    if not details:
        return {"status": "COMMITTED"}
    commit = _git(project, "rev-parse", "HEAD").strip()
    return {"status": "COMMITTED", "commit": commit, "branch": branch}


def exact_child_commit(project: Path, parent: str, paths: set[str],
                       head: str | None = None) -> bool:
    """Check a single-parent commit's complete path set without rename elision."""
    head = head or _git(project, "rev-parse", "HEAD").strip()
    parents = _git(project, "rev-list", "--parents", "-n", "1", head).split()
    if len(parents) != 2 or parents[1] != parent:
        return False
    changed = {path for path in _git(project, "diff-tree", "--no-commit-id",
                                    "--no-renames", "--name-only", "-r", "-z",
                                    head).split("\0") if path}
    return changed == paths


def changed_paths(project: Path, *revisions: str) -> set[str]:
    """Return both sides of renames in a bounded, normalized Git diff."""
    output = _git(project, "diff", "--no-renames", "--name-only", "-z", *revisions)
    paths = set()
    for raw in output.split("\0"):
        if not raw:
            continue
        paths.add(safe_changed_path(raw))
    return paths


def safe_changed_path(raw: str) -> str:
    """Normalize one Git-reported path without accepting traversal."""
    path = raw.replace("\\", "/")
    pure = PurePosixPath(path)
    require(bool(path) and not pure.is_absolute() and ".." not in pure.parts,
            "Git reported an unsafe changed path")
    return path


def finish_feature(project: Path, feature_branch: str, target_branch: str) -> dict:
    """Switch to the original branch and perform a local no-fast-forward merge."""
    project = Path(project).resolve()
    require(isinstance(feature_branch, str) and (FEATURE.fullmatch(feature_branch)
            or BASE_BRANCH.fullmatch(feature_branch)) is not None,
            "Invalid feature branch")
    require(_valid_branch(target_branch), "Invalid target branch")
    _require_clean(project)
    current = _git(project, "branch", "--show-current").strip()
    require(current == feature_branch, "Current branch is not the expected feature branch")
    _git(project, "show-ref", "--verify", f"refs/heads/{target_branch}")
    _git(project, "switch", target_branch)
    try:
        _git(project, "merge", "--no-ff", "--no-edit",
             "--no-verify", feature_branch)
    except StoryLoopError as exc:
        raise StoryLoopError("Git merge conflict or failure; feature branch was preserved",
                             code="GIT_CONFLICT") from exc
    return {"status": "MERGED", "source": feature_branch, "target": target_branch,
            "merge_commit": _git(project, "rev-parse", "HEAD").strip()}


def current_branch(project: Path) -> str:
    return _git(Path(project).resolve(), "branch", "--show-current").strip()


def _require_clean(project: Path) -> None:
    status = _git(project, "status", "--porcelain=v1", "--untracked-files=all")
    require(not status.strip(), "Git worktree must be clean")


def _safe_path(value: object) -> str:
    normalized = repository_relative(value, "commit path")
    require(normalized.split("/", 1)[0] != ".git", "Unsafe commit path")
    return normalized


def _valid_branch(value: object) -> bool:
    return (isinstance(value, str) and BRANCH.fullmatch(value) is not None
            and ".." not in value and "//" not in value and not value.endswith("/"))
