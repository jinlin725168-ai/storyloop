"""Disposable detached checkout for one base agent attempt."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import tempfile
import uuid
from collections.abc import Callable

from .git_command import git
from .models import StoryLoopError, identifier, require
from .run_state import save_run
from .storage import load_json


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


def _external_temp_root(project: Path, root: Path) -> Path:
    for candidate in (root, *root.parents):
        base = candidate / "storyloop-agent-wt"
        if not base.is_relative_to(project) and not project.is_relative_to(base):
            return candidate
    raise StoryLoopError("No external temporary root is available for the agent worktree")


def _registered(project: Path, path: Path) -> bool:
    listing = git(project, "worktree", "list", "--porcelain")
    return any(Path(line[9:]).resolve() == path for line in listing.splitlines()
               if line.startswith("worktree "))


def _anchored_gitdir(project: Path, state: dict) -> tuple[Path, Path]:
    anchor = state["agent_worktree"]
    path = _outside_project(project, _safe_temp_root(Path(anchor["path"])))
    common = _common_dir(project)
    expected = common / "worktrees" / anchor["attempt_id"]
    gitdir = Path(anchor.get("gitdir", ""))
    require(gitdir == expected and gitdir.is_dir() and not gitdir.is_symlink(),
            "Agent worktree Git directory is not the saved registration")
    require(_registered(project, path), "Agent worktree is not registered")
    try:
        recorded = (gitdir / "gitdir").read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise StoryLoopError("Agent worktree Git registration is unreadable",
                             code="WORKSPACE_TAINTED") from exc
    registered_path = Path(recorded)
    if not registered_path.is_absolute():
        registered_path = gitdir / registered_path
    require(registered_path.resolve() == path / ".git",
            "Agent worktree Git registration points elsewhere")
    head_file = gitdir / "HEAD"
    require(head_file.is_file() and not head_file.is_symlink(),
            "Agent worktree HEAD is unsafe")
    head = head_file.read_text(encoding="ascii").strip()
    require(len(head) in {40, 64} and all(c in "0123456789abcdef" for c in head),
            "Agent worktree HEAD no longer names a detached commit")
    require(git(project, "merge-base", "--is-ancestor", anchor["contract_commit"], head) == "",
            "Agent worktree HEAD left the approved contract history")
    return path, gitdir


def agent_git_session(project: Path, state: dict) -> tuple[Path, str, Callable[..., str]]:
    """Pin one audited checkout and its executable Git configuration for transfer."""
    path, gitdir = _anchored_gitdir(project, state)
    names = git(project, "config", "--list", "--name-only").splitlines()
    filters = sorted({name for name in names if name.startswith("filter.")
                      and name.rsplit(".", 1)[-1] in {"process", "clean", "smudge"}})
    drivers = sorted({name.rsplit(".", 1)[0] for name in filters})
    disabled = [part for name in filters for part in ("-c", f"{name}=")]
    disabled += [part for name in drivers for part in ("-c", f"{name}.required=false")]

    def run(*arguments: str) -> str:
        return git(project, f"--git-dir={gitdir}", f"--work-tree={path}",
                   "-c", "core.fsmonitor=false", "-c", "diff.external=",
                   *disabled, *arguments)

    return path, run("rev-parse", "HEAD").strip(), run


def _private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, exist_ok=True)
    require(not path.is_symlink() and not (hasattr(path, "is_junction") and path.is_junction()),
            "Agent worktree parent contains a symbolic link or junction")
    if os.name == "posix":
        require(path.stat().st_uid == os.getuid(), "Agent worktree parent belongs to another user")
        path.chmod(0o700)


def _worktree_timeout(project: Path) -> int:
    config = project / ".storyloop/config.json"
    if config.is_file():
        configured = load_json(config).get("check_timeout_seconds")
        if type(configured) is int and configured > 0:
            return max(180, min(configured, 3600))
    return 180


def _check_checkout_paths(project: Path, contract: str, path: Path) -> None:
    if os.name != "nt":
        return
    tracked = git(project, "ls-tree", "-r", "--name-only", "-z", contract)
    for relative in tracked.split("\0"):
        if relative:
            candidate = str(path / relative)
            length = len(candidate.encode("utf-16-le")) // 2
            require(length <= 240, f"Agent worktree path is too long: {relative}")


def create_agent_worktree(project: Path, state: dict) -> Path:
    """Save the only cleanup anchor before asking Git to create a detached checkout."""
    project = Path(project).resolve()
    require(not state.get("agent_worktree"), "An agent worktree attempt is already anchored")
    require(not git(project, "status", "--porcelain=v1", "--untracked-files=all").strip(),
            "Agent base implementation needs a clean main worktree")
    require(git(project, "rev-parse", "HEAD").strip() == state["contract_commit"],
            "Agent base implementation needs the committed contract")
    change_id = identifier(state["change_id"], "change id")
    common = _common_dir(project)
    repo = hashlib.sha256(str(common).encode("utf-8")).hexdigest()[:12]
    attempt = uuid.uuid4().hex[:12]
    temp_root = _external_temp_root(project, _safe_temp_root(Path(tempfile.gettempdir()).resolve()))
    base = _outside_project(project, temp_root / "storyloop-agent-wt")
    path = _outside_project(project, base / repo / change_id / attempt)
    if os.name == "nt":
        require(len(str(path).encode("utf-16-le")) // 2 <= 240,
                "Agent worktree path is too long")
    _check_checkout_paths(project, state["contract_commit"], path)
    for folder in (base, base / repo, path.parent):
        _private_directory(folder)
    require(not path.exists(), "Agent attempt worktree path already exists")
    state["agent_worktree"] = {"path": str(path), "contract_commit": state["contract_commit"],
                               "attempt_id": attempt, "gitdir": str(common / "worktrees" / attempt)}
    save_run(project, state)
    git(project, "worktree", "add", "--detach", str(path), state["contract_commit"],
        timeout_seconds=_worktree_timeout(project))
    _anchored_gitdir(project, state)
    return path


def remove_agent_worktree(project: Path, state: dict) -> None:
    """Remove only the registered checkout named by this run, never an inferred directory."""
    anchor = state.get("agent_worktree")
    if not anchor:
        return
    project = Path(project).resolve()
    path = Path(anchor["path"])
    try:
        require(anchor.get("contract_commit") == state.get("contract_commit"),
                "Agent worktree contract anchor changed")
        path = _outside_project(project, _safe_temp_root(path))
        registered = _registered(project, path)
        if not path.exists() and not registered:
            state.pop("agent_worktree", None)
            return
        if not registered:
            if path.is_dir() and not any(path.iterdir()):
                path.rmdir()
                state.pop("agent_worktree", None)
                return
            raise StoryLoopError("Agent worktree path is not registered; preserve it")
    except (StoryLoopError, OSError) as exc:
        raise StoryLoopError(
            f"Agent worktree anchor is unsafe: {exc}. Preserve the directory, inspect the "
            "saved anchor and Git registration, then storyloop reset <project> "
            f"--change {state['change_id']} --reason '<reason>'",
            code="AGENT_ANCHOR_INVALID",
        ) from exc
    try:
        git(project, "worktree", "remove", "--force", str(path),
            timeout_seconds=_worktree_timeout(project))
        git(project, "worktree", "prune", timeout_seconds=_worktree_timeout(project))
    except (StoryLoopError, OSError) as exc:
        resume = agent_cleanup_next(state)
        raise StoryLoopError(
            f"Agent worktree cleanup failed: {exc}. Close the agent or IDE, then run "
            f"git worktree remove --force {path}; git worktree prune; {resume}",
            code="WORKSPACE_TAINTED",
        ) from exc
    state.pop("agent_worktree", None)


def agent_cleanup_next(state: dict) -> str:
    if state.get("candidate_commit") and state.get("base_inflight") == "candidate_verification":
        return f"storyloop run <project> --change {state['change_id']}"
    return f"storyloop reset <project> --change {state['change_id']} --reason '<reason>'"
