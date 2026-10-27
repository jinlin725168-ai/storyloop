"""Move only audited final file bytes out of a disposable agent checkout."""

from __future__ import annotations

from pathlib import Path
from collections.abc import Callable
import stat

from .base_agent_worktree import agent_git_session
from .git_command import git
from .models import require
from .path_security import safe_target
from .project_boundaries import audit_base_scope
from .project_git import commit_paths, safe_changed_path
from .storage import write_bytes_atomic

AGENT_NAME = "StoryLoop Agent"
AGENT_EMAIL = "agent@storyloop.local"


def _tree_mode(agent_git: Callable[..., str], commit: str, relative: str) -> str | None:
    output = agent_git("ls-tree", "-z", commit, "--", relative)
    for entry in output.split("\0"):
        if entry and entry.partition("\t")[2] == relative:
            return entry.split(" ", 1)[0]
    return None


def _candidate_paths(agent_git: Callable[..., str], contract: str) -> set[str]:
    changed = agent_git("diff", "--ignore-submodules=all", "--no-renames", "--no-ext-diff",
                        "--no-textconv", "--name-only", "-z", contract)
    untracked = agent_git("ls-files", "--others", "--exclude-standard", "-z")
    return {safe_changed_path(path) for output in (changed, untracked)
            for path in output.split("\0") if path}


def _effective_mode(agent_git: Callable[..., str], contract: str, relative: str) -> str:
    raw = agent_git("diff", "--ignore-submodules=all", "--raw", "--no-renames", "--no-ext-diff",
                    "--no-textconv", "-z", contract, "--", relative)
    if not raw:
        return "100644"  # An unstaged new regular file is copied back as a normal file.
    header = raw.split("\0", 1)[0].split()
    require(len(header) >= 2 and header[1] in {"100644", "100755"},
            f"Agent candidate has unsafe Git mode: {relative}")
    return header[1]


def _validated_bytes(agent_git: Callable[..., str], checkout: Path,
                     contract: str, relative: str) -> tuple[bytes | None, str | None]:
    require(not (checkout / relative).is_symlink(), f"Agent candidate has unsafe link: {relative}")
    path = safe_target(checkout, relative, "agent candidate path")
    old_mode = _tree_mode(agent_git, contract, relative)
    require(old_mode in {None, "100644", "100755"}, f"Agent candidate has unsafe mode: {relative}")
    if not path.exists():
        require(not path.is_symlink(), f"Agent candidate has unsafe link: {relative}")
        return None, None
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode), f"Agent candidate has unsafe link or file type: {relative}")
    mode = _effective_mode(agent_git, contract, relative)
    require(old_mode == mode if old_mode else mode == "100644",
            f"Agent candidate changed file mode: {relative}")
    content = path.read_bytes()
    after = path.lstat()
    require((after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_mode)
            == (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_mode),
            f"Agent candidate changed during audit: {relative}")
    return content, mode


def transfer_agent_candidate(project: Path, state: dict, change: dict) -> str:
    """Audit the disposable tree, copy approved bytes, then create one engine commit."""
    project = Path(project).resolve()
    contract = state["contract_commit"]
    require(git(project, "rev-parse", "HEAD").strip() == contract,
            "Agent transfer needs the committed contract")
    checkout, agent_head, agent_git = agent_git_session(project, state)
    expected = set(change["product"]["base"]["paths"])
    actual = _candidate_paths(agent_git, contract)
    extra = sorted(actual - expected)
    require(not extra, "Agent wrote outside approved base paths: " + ", ".join(extra))
    require(actual == expected, "Agent did not implement every approved base path")
    audit_base_scope(project, change, actual)
    content = {relative: _validated_bytes(agent_git, checkout, contract, relative)
               for relative in sorted(actual)}
    require(agent_git("rev-parse", "HEAD").strip() == agent_head,
            "Agent worktree HEAD changed during audit")
    require(not git(project, "status", "--porcelain=v1", "--untracked-files=all").strip(),
            "Agent transfer needs a clean main worktree")
    for relative, (data, mode) in content.items():
        target = safe_target(project, relative, "approved base candidate")
        if data is None:
            target.unlink()
        else:
            write_bytes_atomic(target, data)
            target.chmod(0o755 if mode == "100755" else 0o644)
    committed = commit_paths(project, sorted(actual),
                             f"feat({change['change_id']}): implement approved base change",
                             identity=(AGENT_NAME, AGENT_EMAIL))
    return committed["commit"]
