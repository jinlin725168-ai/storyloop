"""Explicit, argv-only Git checks and no-ff merge operation."""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Callable
from pathlib import Path

from .models import StoryLoopError, require
from .processutil import run_process

FEATURE = re.compile(r"feature/[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}")
Runner = Callable[[list[str], Path], dict]


def ensure_clean_feature_branch(
    project: Path, expected_branch: str, runner: Runner | None = None,
    executable: str | None = None,
) -> dict:
    """Require an exact feature branch and a completely clean worktree."""
    require(isinstance(expected_branch, str) and FEATURE.fullmatch(expected_branch),
            "Invalid feature branch")
    project, git, execute = _setup(project, runner, executable)
    status = _git(execute, _argv(git, "status", "--porcelain=v1",
                                 "--untracked-files=all"), project)
    require(not status["stdout"].strip(), "Git worktree must be clean")
    branch = _git(execute, _argv(git, "branch", "--show-current"), project)
    require(branch["stdout"].strip() == expected_branch,
            "Current Git branch does not match the exact feature branch")
    return {"branch": expected_branch, "clean": True}


def merge_no_ff(
    project: Path, feature_branch: str, target_branch: str,
    runner: Runner | None = None, executable: str | None = None,
) -> dict:
    """Merge one local feature branch into the current target; never push."""
    require(isinstance(feature_branch, str) and FEATURE.fullmatch(feature_branch),
            "Invalid feature branch")
    require(isinstance(target_branch, str) and REF.fullmatch(target_branch)
            and ".." not in target_branch and "//" not in target_branch,
            "Invalid target branch")
    project, git, execute = _setup(project, runner, executable)
    status = _git(execute, _argv(git, "status", "--porcelain=v1",
                                 "--untracked-files=all"), project)
    require(not status["stdout"].strip(), "Git worktree must be clean")
    branch = _git(execute, _argv(git, "branch", "--show-current"), project)
    require(branch["stdout"].strip() == target_branch,
            "Current Git branch does not match the exact target branch")
    _git(execute, _argv(git, "show-ref", "--verify",
                        f"refs/heads/{feature_branch}"), project)
    result = execute(_argv(git, "merge", "--no-ff", "--no-edit", "--no-verify",
                           feature_branch), project)
    if result.get("returncode") != 0:
        raise StoryLoopError("Git merge conflict or failure; resolve it explicitly",
                             code="GIT_CONFLICT")
    return {"status": "MERGED", "source": feature_branch, "target": target_branch}


def _setup(project: Path, runner: Runner | None,
           executable: str | None) -> tuple[Path, str, Runner]:
    project = Path(project).resolve()
    require(project.is_dir(), "Git project must be an existing directory")
    git = executable or shutil.which("git")
    require(isinstance(git, str) and bool(git), "Git executable is unavailable")
    return project, git, runner or _run


def _argv(git: str, *arguments: str) -> list[str]:
    return [git, "-c", f"core.hooksPath={os.devnull}", *arguments]


def _git(runner: Runner, argv: list[str], project: Path) -> dict:
    result = runner(argv, project)
    if result.get("returncode") != 0:
        raise StoryLoopError("Git operation failed", code="GIT_BLOCKED")
    stdout = result.get("stdout")
    require(isinstance(stdout, str) and len(stdout) <= 1024 * 1024,
            "Git output is invalid or too large")
    return result


def _run(argv: list[str], project: Path) -> dict:
    result = run_process(
        argv, project, 30, environment_allowlist=["PATH", "SYSTEMROOT"],
        source_environment=os.environ,
    )
    if result.get("contained") is not True:
        raise StoryLoopError("Git process was not securely contained",
                             code="UNSAFE_CONTAINMENT")
    if result.get("timed_out") or result.get("truncated") or result.get("error"):
        raise StoryLoopError("Git process failed safely", code="GIT_BLOCKED")
    return result
