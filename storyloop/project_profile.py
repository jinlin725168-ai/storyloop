"""Language-neutral discovery for an existing project repository."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil

from .models import StoryLoopError, require
from .path_security import repository_relative

CI_FILES = (
    ".gitlab-ci.yml", "Jenkinsfile", "azure-pipelines.yml", ".circleci/config.yml",
)
GENERATED_PATHS = ("openspec/changes", "openspec/specs", "acceptance/smoke", "acceptance/ui", "reports/storyloop")


SKIP_DIRECTORIES = {"node_modules", "target", "build", "dist", "vendor", "venv", ".venv", "__pycache__"}


def detect_project(root: Path) -> dict:
    """Detect common build systems at the root and one level below, language-neutrally."""
    root = Path(root).resolve()
    require(root.is_dir(), "Project must be an existing directory")
    kinds: list[str] = []
    commands: list[list[str]] = []
    integration: list[str] = []
    for prefix in ["", *_subdirectories(root)]:
        found_kinds, found_commands, found_integration = _detect_in(root, prefix)
        kinds.extend(kind for kind in found_kinds if kind not in kinds)
        commands.extend(found_commands)
        integration.extend(found_integration)
    commands, warnings = _safe_detected_commands(root, commands)
    return {
        "version": 1,
        "kinds": kinds or ["generic"],
        "test_commands": _deduplicate_commands(commands),
        "module_roots": ["modules", "features"],
        "integration_paths": sorted(set(integration)),
        "protected_paths": _protected_files(root),
        "generated_paths": list(GENERATED_PATHS),
        "candidate_allow": [],
        "scratch_paths": [],
        "warnings": warnings,
    }


def validate_project_settings(root: Path, overrides: dict | None = None) -> dict:
    """Apply explicit settings while keeping paths relative and commands argv-only."""
    require(overrides is None or isinstance(overrides, dict), "Project overrides must be an object")
    profile = detect_project(root)
    overrides = overrides or {}
    allowed = {"test_commands", "module_roots", "integration_paths", "candidate_allow",
               "scratch_paths"}
    require(set(overrides) <= allowed, "Unknown project setting")
    if "test_commands" in overrides:
        profile["test_commands"] = _commands(overrides["test_commands"])
    for key in ("module_roots", "integration_paths", "candidate_allow"):
        if key in overrides:
            profile[key] = _relative_paths(overrides[key], key)
    if "scratch_paths" in overrides:
        profile["scratch_paths"] = _relative_paths(overrides["scratch_paths"], "scratch_paths")
    protected = set(profile["protected_paths"])
    require(not protected.intersection(profile["module_roots"]),
            "Protected CI paths cannot be module roots")
    return profile


def is_ci_path(relative_path: str) -> bool:
    """Return whether a normalized repository path belongs to CI configuration."""
    value = relative_path.replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    value = value.rstrip("/")
    return (value == ".github/workflows"
            or value.startswith(".github/workflows/") or value in CI_FILES)


def _commands(value: object) -> list[list[str]]:
    require(isinstance(value, list), "test_commands must be a list of argv arrays")
    result: list[list[str]] = []
    for command in value:
        require(isinstance(command, list) and bool(command),
                "Each command must be a nonempty argv array")
        require(all(isinstance(arg, str) and bool(arg) for arg in command),
                "Every argv item must be nonempty text")
        require(len(command) <= 100, "Command argv is too long")
        result.append(command)
    return result


def _relative_paths(value: object, label: str) -> list[str]:
    require(isinstance(value, list), f"{label} must be a list")
    result: list[str] = []
    for item in value:
        require(isinstance(item, str) and bool(item.strip()), f"{label} needs relative paths")
        normalized = repository_relative(item, label)
        require(not is_ci_path(normalized), "CI configuration paths are always protected")
        result.append(normalized)
    return sorted(set(result))


def _protected_files(root: Path) -> list[str]:
    values = [item for item in CI_FILES if (root / item).is_file()]
    workflows = root / ".github" / "workflows"
    if workflows.is_dir():
        values.extend(path.relative_to(root).as_posix()
                      for path in workflows.rglob("*") if path.is_file())
    return sorted(set(values))


def _json_object(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise StoryLoopError(f"Cannot inspect {path.name}") from exc
    require(isinstance(value, dict), f"{path.name} must contain a JSON object")
    return value


def _package_manager(root: Path) -> str:
    if (root / "pnpm-lock.yaml").is_file():
        return "pnpm"
    if (root / "yarn.lock").is_file():
        return "yarn"
    return "npm"


def _first(root: Path, *names: str) -> str | None:
    return next((name for name in names if (root / name).is_file()), None)


def _existing(root: Path, *names: str) -> list[str]:
    return [name for name in names if (root / name).is_file()]


def _deduplicate_commands(commands: list[list[str]]) -> list[list[str]]:
    seen: set[tuple[str, ...]] = set()
    result: list[list[str]] = []
    for command in commands:
        key = tuple(command)
        if key not in seen:
            seen.add(key)
            result.append(command)
    return result


def _subdirectories(root: Path) -> list[str]:
    """Immediate child directories that may hold a sub-project (monorepo layouts)."""
    return sorted(
        f"{path.name}/" for path in root.iterdir()
        if path.is_dir() and not path.is_symlink() and not path.name.startswith(".")
        and path.name not in SKIP_DIRECTORIES
    )


def _detect_in(root: Path, prefix: str) -> tuple[list[str], list[list[str]], list[str]]:
    """Detect build systems in one directory; `prefix` is '' for the root or 'sub/'."""
    base = root / prefix if prefix else root
    kinds: list[str] = []
    commands: list[list[str]] = []
    integration: list[str] = []
    if (base / "pom.xml").is_file():
        kinds.append("maven")
        integration.append(prefix + "pom.xml")
        executable = "mvnw.cmd" if os.name == "nt" and (base / "mvnw.cmd").is_file() else "mvn"
        commands.append([executable, "-f", prefix + "pom.xml", "test"] if prefix
                        else [executable, "test"])
    gradle_file = _first(base, "build.gradle", "build.gradle.kts")
    if gradle_file:
        kinds.append("gradle")
        integration.extend(prefix + item for item in
                           _existing(base, "settings.gradle", "settings.gradle.kts", gradle_file))
        executable = "gradlew.bat" if os.name == "nt" and (base / "gradlew.bat").is_file() else "gradle"
        commands.append([executable, "-p", prefix.rstrip("/"), "test"] if prefix
                        else [executable, "test"])
    if (base / "package.json").is_file():
        kinds.append("node")
        integration.extend(prefix + item for item in
                           _existing(base, "package.json", "pnpm-workspace.yaml"))
        package = _json_object(base / "package.json")
        if isinstance(package.get("scripts"), dict) and package["scripts"].get("test"):
            commands.append(_node_test_command(base, prefix))
    if _first(base, "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt"):
        kinds.append("python")
        integration.extend(prefix + item for item in
                           _existing(base, "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt"))
        commands.append(["{python}", "-m", "unittest", "discover", "-s", prefix.rstrip("/"),
                         "-t", prefix.rstrip("/")] if prefix
                        else ["{python}", "-m", "unittest", "discover"])
    return kinds, commands, integration


def _node_test_command(base: Path, prefix: str) -> list[str]:
    manager = _package_manager(base)
    if not prefix:
        return [manager, "test"]
    directory = prefix.rstrip("/")
    flags = {"pnpm": ["--dir", directory], "yarn": ["--cwd", directory], "npm": ["--prefix", directory]}
    return [manager, *flags.get(manager, ["--prefix", directory]), "test"]


def _safe_detected_commands(
    root: Path, commands: list[list[str]],
) -> tuple[list[list[str]], list[str]]:
    accepted: list[list[str]] = []
    warnings: list[str] = []
    for command in _deduplicate_commands(commands):
        if command[0] == "{python}":
            accepted.append(command)
            continue
        local = root / command[0]
        resolved = str(local) if local.is_file() else shutil.which(command[0])
        suffix = Path(resolved).suffix.lower() if resolved else ""
        if not resolved or (os.name == "nt" and suffix in {".cmd", ".bat", ".ps1"}):
            warnings.append(
                f"Skipped unsafe or unavailable auto-detected command: {command[0]}"
            )
            continue
        accepted.append(command)
    return accepted, warnings
