"""Disposable detached checkout for one base agent attempt."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import tempfile
import uuid

from .git_command import git
from .models import StoryLoopError, identifier, require
from .run_state import save_run


def _common_dir(project: Path) -> Path:
    raw = git(project, "rev-parse", "--git-common-dir").strip()
    return (project / raw).resolve()


def _outside_project(project: Path, path: Path) -> Path:
    project, path = project.resolve(), path.resolve()
    require(not path.is_relative_to(project), "Agent worktree must be outside the project")
    require(not project.is_relative_to(path), "Agent worktree cannot contain the project")
    return path


def _safe_temp_root(root: Path) -> Path:
    for part in (*reversed(root.parents), root):
        require(not part.is_symlink() and not (hasattr(part, "is_junction") and part.is_junction()),
                "Agent temporary root contains a symbolic link or junction")
    return root.resolve()


def _registered(project: Path, path: Path) -> bool:
    listing = git(project, "worktree", "list", "--porcelain")
    return any(Path(line[9:]).resolve() == path for line in listing.splitlines()
               if line.startswith("worktree "))


def _private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, exist_ok=True)
    require(not path.is_symlink() and not (hasattr(path, "is_junction") and path.is_junction()),
            "Agent worktree parent contains a symbolic link or junction")
    if os.name == "posix":
        require(path.stat().st_uid == os.getuid(), "Agent worktree parent belongs to another user")
        path.chmod(0o700)


def create_agent_worktree(project: Path, state: dict) -> Path:
    """Save the only cleanup anchor before asking Git to create a detached checkout."""
    project = Path(project).resolve()
    require(not state.get("agent_worktree"), "An agent worktree attempt is already anchored")
    require(not git(project, "status", "--porcelain=v1", "--untracked-files=all").strip(),
            "Agent base implementation needs a clean main worktree")
    require(git(project, "rev-parse", "HEAD").strip() == state["contract_commit"],
            "Agent base implementation needs the committed contract")
    change_id = identifier(state["change_id"], "change id")
    repo = hashlib.sha256(str(_common_dir(project)).encode("utf-8")).hexdigest()[:12]
    attempt = uuid.uuid4().hex[:12]
    temp_root = _safe_temp_root(Path(tempfile.gettempdir()))
    base = _outside_project(project, temp_root / "storyloop-agent-wt")
    path = _outside_project(project, base / repo / change_id / attempt)
    require(len(str(path)) <= 240, "Agent worktree path is too long")
    for folder in (base, base / repo, path.parent):
        _private_directory(folder)
    require(not path.exists(), "Agent attempt worktree path already exists")
    state["agent_worktree"] = {"path": str(path), "contract_commit": state["contract_commit"],
                               "attempt_id": attempt}
    save_run(project, state)
    git(project, "worktree", "add", "--detach", str(path), state["contract_commit"])
    require(_registered(project, path) and _common_dir(path) == _common_dir(project),
            "Agent worktree registration does not match its saved anchor")
    return path


def remove_agent_worktree(project: Path, state: dict) -> None:
    """Remove only the registered checkout named by this run, never an inferred directory."""
    anchor = state.get("agent_worktree")
    if not anchor:
        return
    project = Path(project).resolve()
    require(anchor.get("contract_commit") == state.get("contract_commit"),
            "Agent worktree contract anchor changed")
    path = _outside_project(project, Path(anchor["path"]))
    require(not Path(anchor["path"]).is_symlink(), "Agent worktree path became a symbolic link")
    if not path.exists() and not _registered(project, path):
        state.pop("agent_worktree", None)
        return
    if not _registered(project, path):
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()
            state.pop("agent_worktree", None)
            return
        raise StoryLoopError("Agent worktree path is not registered; preserve and inspect it",
                             code="WORKSPACE_TAINTED")
    require(_common_dir(path) == _common_dir(project),
            "Agent worktree belongs to another Git repository")
    try:
        git(project, "worktree", "remove", "--force", str(path))
        git(project, "worktree", "prune")
    except StoryLoopError as exc:
        raise StoryLoopError(
            f"Agent worktree cleanup failed: {exc}. Close the agent or IDE, then run "
            f"git worktree remove --force {path}; git worktree prune; "
            f"storyloop reset <project> --change {state['change_id']} --reason '<reason>'",
            code="WORKSPACE_TAINTED",
        ) from exc
    state.pop("agent_worktree", None)
