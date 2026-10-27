"""Move only audited final file bytes out of a disposable agent checkout."""

from __future__ import annotations

from pathlib import Path
import stat

from .git_command import git
from .models import require
from .path_security import safe_target
from .project_boundaries import audit_base_scope
from .project_git import changed_paths, commit_paths, safe_changed_path
from .storage import write_bytes_atomic

AGENT_NAME = "StoryLoop Agent"
AGENT_EMAIL = "agent@storyloop.local"


def _tree_mode(checkout: Path, commit: str, relative: str) -> str | None:
    output = git(checkout, "ls-tree", "-z", commit, "--", relative)
    for entry in output.split("\0"):
        if entry and entry.partition("\t")[2] == relative:
            return entry.split(" ", 1)[0]
    return None


def _candidate_paths(checkout: Path, contract: str) -> set[str]:
    changed = changed_paths(checkout, contract)
    untracked = git(checkout, "ls-files", "--others", "--exclude-standard", "-z")
    return changed | {safe_changed_path(path) for path in untracked.split("\0") if path}


def _validated_bytes(checkout: Path, contract: str, relative: str) -> bytes | None:
    require(not (checkout / relative).is_symlink(), f"Agent candidate has unsafe link: {relative}")
    path = safe_target(checkout, relative, "agent candidate path")
    old_mode = _tree_mode(checkout, contract, relative)
    require(old_mode in {None, "100644", "100755"}, f"Agent candidate has unsafe mode: {relative}")
    if not path.exists():
        require(not path.is_symlink(), f"Agent candidate has unsafe link: {relative}")
        return None
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode), f"Agent candidate has unsafe link or file type: {relative}")
    executable = bool(info.st_mode & 0o111)
    require((old_mode == "100755") == executable if old_mode else not executable,
            f"Agent candidate changed file mode: {relative}")
    content = path.read_bytes()
    after = path.lstat()
    require((after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_mode)
            == (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_mode),
            f"Agent candidate changed during audit: {relative}")
    return content


def transfer_agent_candidate(project: Path, checkout: Path, change: dict, contract: str) -> str:
    """Audit the disposable tree, copy approved bytes, then create one engine commit."""
    project, checkout = Path(project).resolve(), Path(checkout).resolve()
    require(git(project, "rev-parse", "HEAD").strip() == contract,
            "Agent transfer needs the committed contract")
    require(not git(project, "status", "--porcelain=v1", "--untracked-files=all").strip(),
            "Agent transfer needs a clean main worktree")
    require(git(checkout, "merge-base", "--is-ancestor", contract, "HEAD") == "",
            "Agent checkout does not descend from the contract")
    expected = set(change["product"]["base"]["paths"])
    actual = _candidate_paths(checkout, contract)
    extra = sorted(actual - expected)
    require(not extra, "Agent wrote outside approved base paths: " + ", ".join(extra))
    require(actual == expected, "Agent did not implement every approved base path")
    audit_base_scope(project, change, actual)
    content = {relative: _validated_bytes(checkout, contract, relative) for relative in sorted(actual)}
    require(not git(project, "status", "--porcelain=v1", "--untracked-files=all").strip(),
            "Agent transfer needs a clean main worktree")
    for relative, data in content.items():
        target = safe_target(project, relative, "approved base candidate")
        if data is None:
            target.unlink()
        else:
            write_bytes_atomic(target, data)
    committed = commit_paths(project, sorted(actual),
                             f"feat({change['change_id']}): implement approved base change",
                             identity=(AGENT_NAME, AGENT_EMAIL))
    return committed["commit"]
