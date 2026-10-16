"""Commit-bound Git evidence for routing scope and reference signals."""
from __future__ import annotations

import subprocess
from pathlib import Path

from .baseline import load_project_config
from .models import StoryLoopError, require
from .path_security import repository_relative, safe_target
from .project_profile import GENERATED_PATHS, detect_project, matches_test_root

MAX_GIT_OUTPUT = 1_048_576
GIT_TIMEOUT = 30
SCOPE_FIELDS = {"touched", "creates", "interfaces", "symbols"}


def measure_scope(project: Path, commit: str, scope: dict, policy: dict) -> dict:
    """Validate declared scope against one commit and return engine-owned signals."""
    project, commit = _repository_and_commit(project, commit)
    profile = _profile(project)
    touched, creates, interfaces, symbols = _validated_scope(project, commit, scope, profile)
    artifacts = _artifact_paths(profile)
    tests = set(path for path in touched + creates if _is_test(path, profile))
    excluded = tests | set(path for path in touched + creates if _inside_any(path, artifacts))
    business_existing = sorted(set(touched) - excluded)
    business_created = sorted(set(creates) - excluded)
    reference_values = [reference_count(project, commit, item["symbol"]) for item in symbols]
    covered = {item["path"] for item in symbols}
    all_covered = set(business_existing) <= covered
    maximum = 0 if not business_existing else _known_max(reference_values, all_covered)
    risks = {key: _risk(key, touched + creates, policy) for key in ("sensitive", "guard")}
    signals = {"existing_files": len(business_existing), "created_files": len(business_created),
               "interfaces": len(set(interfaces)), "max_references": maximum,
               "risks": risks, "complete": all_covered and maximum is not None}
    locations = business_existing + business_created
    evidence = [_evidence(field, signals[field], locations, commit)
                for field in ("existing_files", "created_files", "interfaces", "max_references")]
    evidence.extend(_evidence(f"risks.{key}", value, locations, commit)
                    for key, value in risks.items())
    evidence.append(_evidence("hotspots", _hotspots(project, commit, business_existing),
                              business_existing, commit))
    return {"signals": signals, "evidence": evidence,
            "scope": {"touched": touched, "creates": creates,
                      "interfaces": interfaces, "symbols": symbols}}


def actual_scope(project: Path, contract_commit: str) -> dict:
    """Report Git-visible working-tree changes relative to the contract commit."""
    project, commit = _repository_and_commit(project, contract_commit)
    existing = _tree_paths(project, commit)
    changed = _name_status(project, commit)
    untracked = _git(project, "ls-files", "--others", "--exclude-standard", "-z")
    paths = {path for path in _nul_paths(untracked)}
    paths.update(path for _, path in changed)
    touched, creates = [], []
    for path in sorted(paths):
        safe_target(project, path, "Git path")
        (touched if path in existing else creates).append(path)
    return {"touched": touched, "creates": creates, "interfaces": [], "symbols": [],
            "unknown_fields": ["interfaces", "symbols"]}


def reference_count(project: Path, commit: str, symbol: str) -> int | None:
    """Count unique business files containing a fixed symbol at one commit."""
    _validate_symbol(symbol)
    try:
        project, commit = _repository_and_commit(project, commit)
        result = _run(project, "grep", "-l", "-z", "-F", "-e", symbol, commit, "--")
    except (StoryLoopError, subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode == 1:
        return 0
    if result.returncode != 0 or len(result.stdout) > MAX_GIT_OUTPUT:
        return None
    profile, prefix = _profile(project), (commit + ":").encode()
    paths = set()
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        if raw.startswith(prefix):
            raw = raw[len(prefix):]
        try:
            path = repository_relative(raw.decode("utf-8"), "Git grep path")
        except (UnicodeDecodeError, StoryLoopError):
            return None
        if _is_business(path, profile):
            paths.add(path)
    return len(paths)


def _validated_scope(project: Path, commit: str, scope: object, profile: dict) -> tuple:
    require(isinstance(scope, dict) and set(scope) == SCOPE_FIELDS, "Invalid routing scope")
    for field in ("touched", "creates", "interfaces", "symbols"):
        require(isinstance(scope[field], list), f"scope.{field} must be a list")
    existing = _tree_paths(project, commit)
    touched = _paths(project, scope["touched"], "touched path")
    creates = _paths(project, scope["creates"], "created path")
    require(set(touched).isdisjoint(creates), "A path cannot be both touched and created")
    require(set(touched) <= existing, "Touched paths must exist at the contract commit")
    require(not set(creates) & existing, "Created paths must not exist at the contract commit")
    interfaces = _texts(scope["interfaces"], "interface", 1000)
    symbols = _symbols(project, scope["symbols"], touched, existing, profile)
    return touched, creates, interfaces, symbols


def _symbols(project: Path, values: list, touched: list[str], existing: set[str], profile: dict) -> list:
    result = []
    for item in values:
        require(isinstance(item, dict) and set(item) == {"path", "symbol"}, "Invalid symbol entry")
        path = _paths(project, [item["path"]], "symbol path")[0]
        require(path in existing and path in touched and _is_business(path, profile),
                "Symbol path must be an existing touched business file")
        _validate_symbol(item["symbol"])
        result.append({"path": path, "symbol": item["symbol"]})
    unique = sorted({(item["path"], item["symbol"]) for item in result})
    return [{"path": path, "symbol": symbol} for path, symbol in unique]


def _paths(project: Path, values: list, label: str) -> list[str]:
    result = []
    for value in values:
        path = repository_relative(value, label)
        safe_target(project, path, label)
        result.append(path)
    return sorted(set(result))


def _texts(values: list, label: str, limit: int) -> list[str]:
    for value in values:
        require(isinstance(value, str) and bool(value.strip()), f"{label} must be nonempty text")
        require("\x00" not in value and "\n" not in value and "\r" not in value,
                f"Invalid {label}")
        require(len(value) <= limit, f"{label} is too long")
    return sorted(set(values))


def _validate_symbol(symbol: object) -> None:
    _texts([symbol] if isinstance(symbol, str) else [symbol], "symbol", 1000)


def _repository_and_commit(project: Path, commit: object) -> tuple[Path, str]:
    project = Path(project).resolve()
    require(project.is_dir() and (project / ".git").is_dir(), "Git project is required")
    require(isinstance(commit, str) and 1 <= len(commit) <= 200, "Invalid commit")
    require("\x00" not in commit and "\n" not in commit and "\r" not in commit, "Invalid commit")
    result = _run(project, "rev-parse", "--verify", "--end-of-options", f"{commit}^{{commit}}")
    require(result.returncode == 0 and len(result.stdout) <= 200, "Unknown Git commit")
    return project, result.stdout.decode("ascii").strip()


def _tree_paths(project: Path, commit: str) -> set[str]:
    output = _git(project, "ls-tree", "-r", "-z", "--name-only", commit)
    return set(_nul_paths(output))


def _name_status(project: Path, commit: str) -> list[tuple[str, str]]:
    parts = _git(project, "diff", "--name-status", "-z", "--no-renames", commit, "--").split(b"\0")
    values = [part for part in parts if part]
    require(len(values) % 2 == 0, "Malformed Git status output")
    return [(values[index].decode("ascii"), _decode_path(values[index + 1]))
            for index in range(0, len(values), 2)]


def _profile(project: Path) -> dict:
    config = project / ".storyloop" / "config.json"
    if config.exists() or config.is_symlink():
        safe_target(project, ".storyloop/config.json", "project config")
        require(config.is_file(), "Invalid project config")
        profile = load_project_config(project).get("profile")
        require(isinstance(profile, dict), "Invalid project profile")
        return profile
    profile = detect_project(project)
    if not profile["test_roots"] and (project / "tests").is_dir():
        profile["test_roots"] = ["tests"]
    return profile


def _artifact_paths(profile: dict) -> list[str]:
    return list(profile.get("generated_paths") or GENERATED_PATHS) + list(profile.get("protected_paths") or [])


def _is_test(path: str, profile: dict) -> bool:
    return matches_test_root(path, list(profile.get("test_roots") or []))


def _is_business(path: str, profile: dict) -> bool:
    return not _is_test(path, profile) and not _inside_any(path, _artifact_paths(profile))


def _inside_any(path: str, roots: list[str]) -> bool:
    return any(path == root.rstrip("/") or path.startswith(root.rstrip("/") + "/") for root in roots)


def _risk(key: str, paths: list[str], policy: dict) -> str:
    roots = policy[f"{key}_paths"]
    return "yes" if any(_inside_any(path, roots) for path in paths) else "no"


def _known_max(values: list[int | None], covered: bool) -> int | None:
    return max(values, default=0) if covered and all(value is not None for value in values) else None


def _hotspots(project: Path, commit: str, paths: list[str]) -> dict[str, int | None]:
    result: dict[str, int | None] = {}
    for path in paths:
        try:
            process = _run(project, "log", "--since=90.days", "--format=", "--name-only",
                           commit, "--", path)
        except (subprocess.TimeoutExpired, OSError):
            result[path] = None
            continue
        if process.returncode or len(process.stdout) > MAX_GIT_OUTPUT:
            result[path] = None
            continue
        result[path] = sum(line == path.encode("utf-8") for line in process.stdout.splitlines())
    return result


def _evidence(field: str, value: object, locations: list[str], commit: str) -> dict:
    return {"field": field, "value": value, "source": "engine",
            "locations": locations, "commit": commit}


def _nul_paths(output: bytes) -> list[str]:
    return [_decode_path(raw) for raw in output.split(b"\0") if raw]


def _decode_path(raw: bytes) -> str:
    try:
        return repository_relative(raw.decode("utf-8"), "Git path")
    except UnicodeDecodeError as exc:
        raise StoryLoopError("Git path is not UTF-8") from exc


def _git(project: Path, *args: str) -> bytes:
    result = _run(project, *args)
    require(result.returncode == 0 and len(result.stdout) <= MAX_GIT_OUTPUT, "Git evidence unavailable")
    return result.stdout


def _run(project: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=project, capture_output=True,
                          timeout=GIT_TIMEOUT, check=False)
