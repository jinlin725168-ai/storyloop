"""Logical foundation baselines for one existing Git repository."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Callable

from .models import StoryLoopError, require  # noqa: F401  # 兼容再导出：保留旧模块导入路径
from .path_security import safe_target
from .git_command import GIT_ENVIRONMENT, git as _git, git_failure as _git_failure  # noqa: F401  # 兼容再导出：保留旧模块导入路径
from .project_profile import GENERATED_PATHS, matches_test_root, validate_project_settings
from .storage import load_json, write_json, write_text_atomic

BASELINE_PATH = Path(".storyloop/baseline.json")
CONFIG_PATH = Path(".storyloop/config.json")


def bootstrap_project(
    project: Path, overrides: dict | None = None, *,
    smoke_preflight: Callable[[Path], None] | None = None,
    smoke_adoption: Callable[[Path], object] | None = None,
    adapter_check: Callable[[Path], object] | None = None,
) -> dict:
    """Capture a clean repository atomically; restore only this attempt's control writes."""
    from .initialization_transaction import initialization_transaction
    from .onboarding_manifest import validate_onboarding
    project = Path(project).resolve()
    _require_initializable(project)
    original_head = _git(project, "rev-parse", "HEAD").strip()
    original_branch = _git(project, "branch", "--show-current").strip()
    settings, budget, role_config = _bootstrap_settings(overrides)
    profile = validate_project_settings(project, settings)
    validate_onboarding(project, profile)
    baseline = _initial_baseline(project, profile)
    require(baseline["source_head"] == original_head and baseline["source_branch"] == original_branch,
            "Repository HEAD or branch changed during initialization validation")
    config = _initial_config(profile, budget, role_config)
    if smoke_preflight is not None:
        smoke_preflight(project)
    with initialization_transaction(project):
        _require_initializable(project)
        require(_git(project, "rev-parse", "HEAD").strip() == original_head
                and _git(project, "branch", "--show-current").strip() == original_branch,
                "Repository HEAD changed during initialization")
        _verify_foundation_files(project, baseline)
        _exclude_control_plane(project)
        write_json(project / CONFIG_PATH, config, max_bytes=512_000)
        write_json(project / BASELINE_PATH, baseline, max_bytes=8_000_000)
        if smoke_adoption is not None:
            smoke_adoption(project)
        if (config.get("adapters") or config.get("roles")) and adapter_check is not None:
            adapter_check(project)
        require(not _git(project, "status", "--porcelain=v1", "--untracked-files=all").strip(),
                "StoryLoop control files must stay outside Git changes")
        require(_git(project, "rev-parse", "HEAD").strip() == original_head
                and _git(project, "branch", "--show-current").strip() == original_branch,
                "Repository HEAD changed during initialization")
    return _bootstrap_result(project, baseline, profile)


def _require_initializable(project: Path) -> None:
    require(project.is_dir(), "Git project must be an existing directory")
    require(not (project / BASELINE_PATH).exists(), "StoryLoop baseline already exists")
    _require_local_git_directory(project)
    top = Path(_git(project, "rev-parse", "--show-toplevel").strip()).resolve()
    require(os.path.normcase(str(top)) == os.path.normcase(str(project)),
            "Project must be the Git repository root")
    require(not _git(project, "status", "--porcelain=v1", "--untracked-files=all").strip(),
            "Git worktree must be clean before initialization")
    require(bool(_git(project, "branch", "--show-current").strip()),
            "Git repository must be on a named branch")



def _require_local_git_directory(project: Path) -> None:
    """Reject external Git control before initialization or draft writes."""
    git_control = safe_target(project, ".git", "Git control path", target_directory=True)
    require(git_control.is_dir(),
            "StoryLoop requires a local .git directory; linked worktrees and separate Git "
            "directories are unsupported. Use an independent clone.")


def _initial_baseline(project: Path, profile: dict) -> dict:
    tracked = _tracked_files(project)
    protected = _foundation_paths(tracked, profile)
    return {
        "version": 1, "baseline_version": "1.0.0",
        "source_head": _git(project, "rev-parse", "HEAD").strip(),
        "source_branch": _git(project, "branch", "--show-current").strip(),
        "files": {relative: _sha256(project / relative) for relative in tracked},
        "foundation_paths": protected, "module_roots": profile["module_roots"],
        "integration_paths": profile["integration_paths"],
        "protected_paths": sorted(set(protected) | set(profile["protected_paths"])),
    }


def _initial_config(profile: dict, budget: dict, role_config: dict) -> dict:
    return {
        "version": 1, "mode": "product-iteration", "adapter": "codex", "agent_command": [],
        "agent_timeout_seconds": 14400, "check_timeout_seconds": 300,
        "design_min_stories": 2, "profile": profile, **budget, **role_config,
    }


def _bootstrap_settings(overrides: dict | None) -> tuple[dict, dict, dict]:
    settings = dict(overrides or {})
    budget = {}
    role_config = {key: settings.pop(key) for key in ("adapters", "roles") if key in settings}
    if 'max_attempts' in settings:
        value = settings.pop('max_attempts')
        require(type(value) is int and value > 0, 'Invalid max_attempts')
        budget['max_attempts'] = value
    return settings, budget, role_config


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
    expected = "feature/" if resume else baseline["source_branch"]
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
    tests = profile.get("test_roots") or []  # earlier changes may merge tests there, outside module_roots
    escaped = [path for path in _tracked_files(project)
               if path not in known and not any(_inside(path, root) for root in roots)
               and not matches_test_root(path, tests)
               and not _inside(path, "openspec/lessons")
               and not _inside(path, "storyloop-custom")]
    require(not escaped, "Tracked files outside feature modules require a base iteration")


def _inside(path: str, root: str) -> bool:
    normalized = root.rstrip("/")
    return path == normalized or path.startswith(normalized + "/")


def _foundation_paths(tracked: list[str], profile: dict) -> list[str]:
    mutable_roots = [*profile["module_roots"], *GENERATED_PATHS]
    test_roots = profile.get("test_roots") or []
    protected = set(profile["integration_paths"]) | set(profile["protected_paths"])
    return sorted(path for path in tracked
                  if not _inside(path, "openspec/lessons")
                  if not _inside(path, "storyloop-custom")
                  if not any(_inside(path, root) for root in mutable_roots)
                  and (path in protected or not matches_test_root(path, test_roots)))


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
        write_text_atomic(exclude, prefix + ".storyloop/\n")
