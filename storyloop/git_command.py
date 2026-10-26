"""Bounded, hook-free Git process primitive shared by repository operations."""

from __future__ import annotations

import os
from pathlib import Path
import shutil

from .errors import StoryLoopError, require
from .processutil import run_process


# Git needs the user's global identity and safe.directory settings; hooks are disabled below.
GIT_ENVIRONMENT = ["PATH", "SYSTEMROOT", "HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "XDG_CONFIG_HOME"]


def git_failure(stderr: str) -> str:
    """Keep Git's own bounded reason in a failed operation message."""
    lines = [line.strip() for line in stderr.splitlines() if line.strip() and not line.startswith("***")]
    reason = " ".join(lines)[:400]
    return f"Git operation failed: {reason}" if reason else "Git operation failed"


def git(project: Path, *arguments: str) -> str:
    executable = shutil.which("git")
    require(bool(executable), "Git executable is unavailable")
    result = run_process(
        [executable, "-c", f"core.hooksPath={os.devnull}", *arguments],
        project,
        30,
        environment_allowlist=GIT_ENVIRONMENT,
        source_environment=os.environ,
    )
    if (result.get("contained") is not True or result.get("timed_out")
            or result.get("truncated") or result.get("error")):
        raise StoryLoopError("Git process failed safely", code="GIT_BLOCKED")
    if result.get("returncode") != 0:
        raise StoryLoopError(git_failure(result.get("stderr", "")), code="GIT_BLOCKED")
    stdout = result.get("stdout")
    require(isinstance(stdout, str) and len(stdout) <= 8_000_000, "Git output is invalid")
    return stdout


def git_is_ancestor(project: Path, ancestor: str, descendant: str) -> bool:
    """Use Git's bounded exit status instead of enumerating a possibly long history."""
    executable = shutil.which("git")
    require(bool(executable), "Git executable is unavailable")
    result = run_process(
        [executable, "-c", f"core.hooksPath={os.devnull}", "merge-base", "--is-ancestor",
         ancestor, descendant],
        project, 30, environment_allowlist=GIT_ENVIRONMENT, source_environment=os.environ,
    )
    if (result.get("contained") is not True or result.get("timed_out")
            or result.get("truncated") or result.get("error")):
        raise StoryLoopError("Git process failed safely", code="GIT_BLOCKED")
    if result.get("returncode") not in {0, 1}:
        raise StoryLoopError(git_failure(result.get("stderr", "")), code="GIT_BLOCKED")
    return result["returncode"] == 0
