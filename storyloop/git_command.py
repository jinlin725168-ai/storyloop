"""Bounded, hook-free Git process primitive shared by repository operations."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

from .errors import StoryLoopError, require
from .git_user_config import explicit_global_options
from .processutil import run_process


GIT_ENVIRONMENT = ["PATH", "SYSTEMROOT", "HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH",
                   "XDG_CONFIG_HOME", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM"]


def git_process_policy(project: Path) -> tuple[list[str], dict[str, str]]:
    """Disable ambient configuration and pass only inert, explicit preferences."""
    prefix = ["-c", f"core.hooksPath={os.devnull}",
              *explicit_global_options(project, os.environ)]
    environment = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull,
                   "GIT_CONFIG_NOSYSTEM": "1"}
    return prefix, environment


def git_failure(stderr: str) -> str:
    """Keep Git's own bounded reason in a failed operation message."""
    lines = [line.strip() for line in stderr.splitlines() if line.strip() and not line.startswith("***")]
    reason = " ".join(lines)[:400]
    return f"Git operation failed: {reason}" if reason else "Git operation failed"


def git(project: Path, *arguments: str) -> str:
    executable = shutil.which("git")
    require(bool(executable), "Git executable is unavailable")
    prefix, environment = git_process_policy(project)
    result = run_process(
        [executable, *prefix, *arguments],
        project,
        30,
        environment_allowlist=GIT_ENVIRONMENT,
        source_environment=environment,
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
    prefix, environment = git_process_policy(project)
    result = run_process(
        [executable, *prefix, "merge-base", "--is-ancestor",
         ancestor, descendant],
        project, 30, environment_allowlist=GIT_ENVIRONMENT, source_environment=environment,
    )
    if (result.get("contained") is not True or result.get("timed_out")
            or result.get("truncated") or result.get("error")):
        raise StoryLoopError("Git process failed safely", code="GIT_BLOCKED")
    if result.get("returncode") not in {0, 1}:
        raise StoryLoopError(git_failure(result.get("stderr", "")), code="GIT_BLOCKED")
    return result["returncode"] == 0


def git_binary(project: Path, *arguments: str) -> bytes:
    """Run binary-producing Git operations under the same config policy as text Git."""
    executable = shutil.which("git")
    require(bool(executable), "Git executable is unavailable")
    prefix, environment = git_process_policy(project)
    filtered = {key: environment[key] for key in GIT_ENVIRONMENT if key in environment}
    result = subprocess.run([executable, *prefix, *arguments], cwd=project, env=filtered,
                            capture_output=True, check=False)
    if result.returncode:
        raise StoryLoopError(git_failure(result.stderr.decode("utf-8", errors="replace")),
                             code="GIT_BLOCKED")
    return result.stdout
