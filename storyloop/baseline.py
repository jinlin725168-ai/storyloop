"""Logical foundation baselines for one existing Git repository."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil

from .models import StoryLoopError, require
from .path_security import safe_target
from .processutil import run_process
from .project_profile import GENERATED_PATHS, validate_project_settings
from .storage import load_json, write_json

BASELINE_PATH = Path(".storyloop/baseline.json")
CONFIG_PATH = Path(".storyloop/config.json")


def bootstrap_project(project: Path, overrides: dict | None = None) -> dict:
    """Capture a clean repository as the immutable logical foundation."""
    project = Path(project).resolve()
    require(project.is_dir(), "Git project must be an existing directory")
    require(not (project / BASELINE_PATH).exists(), "StoryLoop baseline already exists")
    top = Path(_git(project, "rev-parse", "--show-toplevel").strip()).resolve()
    require(os.path.normcase(str(top)) == os.path.normcase(str(project)),
            "Project must be the Git repository root")
    require(not _git(project, "status", "--porcelain=v1", "--untracked-files=all").strip(),
            "Git worktree must be clean before initialization")
    profile = validate_project_settings(project, overrides)
    tracked = _tracked_files(project)
    files = {relative: _sha256(project / relative) for relative in tracked}
    source_head = _git(project, "rev-parse", "HEAD").strip()
    source_branch = _git(project, "branch", "--show-current").strip()
    require(bool(source_branch), "Git repository must be on a named branch")
    protected = _foundation_paths(tracked, profile)
    _exclude_control_plane(project)
    config = {
        "version": 1,
        "mode": "product-iteration",
        "adapter": "codex",
        "agent_command": [],
        "max_attempts": 3,
        "agent_timeout_seconds": 14400,  # liveness guard only; the budget is max_attempts
        "check_timeout_seconds": 300,
        "profile": profile,
    }
    baseline = {
        "version": 1,
        "baseline_version": "1.0.0",
        "source_head": source_head,
        "source_branch": source_branch,
        "files": files,
        "foundation_paths": protected,
        "module_roots": profile["module_roots"],
        "integration_paths": profile["integration_paths"],
        "protected_paths": sorted(set(protected) | set(profile["protected_paths"])),
    }
    write_json(project / CONFIG_PATH, config, max_bytes=512_000)
    write_json(project / BASELINE_PATH, baseline, max_bytes=8_000_000)
    from .system_smoke import adopt_system_smoke  # a committed system suite of a clean tree is trusted at init
    adopt_system_smoke(project)
    require(not _git(project, "status", "--porcelain=v1", "--untracked-files=all").strip(),
            "StoryLoop control files must stay outside Git changes")
    return _bootstrap_result(project, baseline, profile)


def _bootstrap_result(project: Path, baseline: dict, profile: dict) -> dict:
    return {"status": "BASELINE_LOCKED", "project": str(project),
            "baseline_version": baseline["baseline_version"],
            "source_head": baseline["source_head"],
            "source_branch": baseline["source_branch"], "warnings": profile["warnings"]}


def load_baseline(project: Path) -> dict:
    """Load and minimally validate one captured baseline."""
    value = load_json(Path(project).resolve() / BASELINE_PATH)
    required = {
        "version", "baseline_version", "source_head", "source_branch", "files",
        "foundation_paths", "module_roots", "integration_paths", "protected_paths",
    }
    require(set(value) == required and value["version"] == 1, "Invalid baseline document")
    require(isinstance(value["files"], dict), "Invalid baseline file map")
    for relative, digest in value["files"].items():
        require(isinstance(relative, str) and isinstance(digest, str) and len(digest) == 64,
                "Invalid baseline digest")
    return value


def load_project_config(project: Path) -> dict:
    """Load the local control-plane configuration."""
    return load_json(Path(project).resolve() / CONFIG_PATH)


def advance_repository_head(project: Path, expected_head: str) -> None:
    """Advance the lineage checkpoint after a verified local feature merge."""
    project = Path(project).resolve()
    require(_git(project, "rev-parse", "HEAD").strip() == expected_head,
            "Repository head changed before baseline lineage update")
    baseline = load_baseline(project)
    baseline["source_head"] = expected_head
    write_json(project / BASELINE_PATH, baseline, max_bytes=8_000_000)


def verify_baseline(project: Path, resume: bool = False) -> dict:
    """Reject foundation drift and hidden index flags before an iteration runs."""
    project = Path(project).resolve()
    baseline = load_baseline(project)
    config = load_project_config(project)
    current = _git(project, "branch", "--show-current").strip()
    expected = f"feature/" if resume else baseline["source_branch"]
    require((current.startswith(expected) if resume else current == expected),
            "Run must start on the locked source branch or its saved feature branch")
    if not resume:
        require(not _git(project, "status", "--porcelain=v1", "--untracked-files=all").strip(),
                "Git worktree must be clean before starting a change")
    _verify_foundation_files(project, baseline)
    _verify_index_flags(project, baseline)
    if not resume:
        _verify_tracked_scope(project, baseline, config["profile"])
    history = set(_git(project, "rev-list", "HEAD").splitlines())
    require(baseline["source_head"] in history, "Locked baseline commit is not an ancestor")
    return {"status": "BASELINE_VERIFIED", "branch": current}


def _verify_foundation_files(project: Path, baseline: dict) -> None:
    for relative in baseline["foundation_paths"]:
        path = safe_target(project, relative, "tracked path")
        require(path.is_file() and not path.is_symlink(),
                f"Foundation file is missing or unsafe: {relative}")
        require(_sha256(path) == baseline["files"][relative],
                f"Foundation drift requires a base iteration: {relative}")


def _verify_index_flags(project: Path, baseline: dict) -> None:
    flags = {}
    for record in _git(project, "ls-files", "-v", "-z").split("\0"):
        if record:
            flags[record[2:].replace("\\", "/")] = record[0]
    hidden = [path for path in baseline["files"] if flags.get(path) != "H"]
    require(not hidden, "Git index flags cannot hide baseline files")


def _verify_tracked_scope(project: Path, baseline: dict, profile: dict) -> None:
    known = set(baseline["files"])
    roots = [*baseline["module_roots"], *profile["generated_paths"]]
    escaped = [path for path in _tracked_files(project)
               if path not in known and not any(_inside(path, root) for root in roots)]
    require(not escaped, "Tracked files outside feature modules require a base iteration")


def _inside(path: str, root: str) -> bool:
    normalized = root.rstrip("/")
    return path == normalized or path.startswith(normalized + "/")


def _foundation_paths(tracked: list[str], profile: dict) -> list[str]:
    mutable_roots = [*profile["module_roots"], *GENERATED_PATHS]
    return sorted(path for path in tracked
                  if not any(_inside(path, root) for root in mutable_roots))


def _tracked_files(project: Path) -> list[str]:
    output = _git(project, "ls-files", "-z")
    result: list[str] = []
    for raw in output.split("\0"):
        if not raw:
            continue
        relative = raw.replace("\\", "/")
        path = safe_target(project, relative, "tracked path")
        require(path.is_file() and not path.is_symlink(),
                "Tracked links and non-files cannot be a foundation input")
        result.append(relative)
    return sorted(result)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _exclude_control_plane(project: Path) -> None:
    exclude = safe_target(project, ".git/info/exclude", "Git exclude path")
    require(exclude.parent.is_dir(), "Git metadata is unavailable")
    current = exclude.read_text(encoding="utf-8-sig") if exclude.exists() else ""
    entries = {line.strip() for line in current.splitlines()}
    if ".storyloop/" not in entries:
        prefix = current
        if prefix and not prefix.endswith("\n"):
            prefix += "\n"
        exclude.write_text(prefix + ".storyloop/\n", encoding="utf-8", newline="\n")


def _git(project: Path, *arguments: str) -> str:
    executable = shutil.which("git")
    require(bool(executable), "Git executable is unavailable")
    result = run_process(
        [executable, "-c", f"core.hooksPath={os.devnull}", *arguments],
        project,
        30,
        environment_allowlist=["PATH", "SYSTEMROOT"],
        source_environment=os.environ,
    )
    if (result.get("contained") is not True or result.get("timed_out")
            or result.get("truncated") or result.get("error")):
        raise StoryLoopError("Git process failed safely", code="GIT_BLOCKED")
    if result.get("returncode") != 0:
        raise StoryLoopError("Git operation failed", code="GIT_BLOCKED")
    stdout = result.get("stdout")
    require(isinstance(stdout, str) and len(stdout) <= 8_000_000, "Git output is invalid")
    return stdout
