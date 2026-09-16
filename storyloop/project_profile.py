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
GENERATED_PATHS = ("openspec/changes", "acceptance/smoke", "reports/storyloop")


def detect_project(root: Path) -> dict:
    """Detect common build systems without assuming one implementation language."""
    root = Path(root).resolve()
    require(root.is_dir(), "Project must be an existing directory")
    kinds: list[str] = []
    commands: list[list[str]] = []
    integration: list[str] = []
    if (root / "pom.xml").is_file():
        kinds.append("maven")
        integration.append("pom.xml")
        commands.append(["mvnw.cmd" if (root / "mvnw.cmd").is_file() else "mvn", "test"])
    gradle_file = _first(root, "build.gradle", "build.gradle.kts")
    if gradle_file:
        kinds.append("gradle")
        integration.extend(_existing(root, "settings.gradle", "settings.gradle.kts", gradle_file))
        commands.append(["gradlew.bat" if (root / "gradlew.bat").is_file() else "gradle", "test"])
    if (root / "package.json").is_file():
        kinds.append("node")
        integration.extend(_existing(root, "package.json", "pnpm-workspace.yaml"))
        package = _json_object(root / "package.json")
        if isinstance(package.get("scripts"), dict) and package["scripts"].get("test"):
            commands.append([_package_manager(root), "test"])
    if _first(root, "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt"):
        kinds.append("python")
        integration.extend(_existing(root, "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt"))
        commands.append(["{python}", "-m", "unittest", "discover"])
    commands, warnings = _safe_detected_commands(root, commands)
    return {
        "version": 1,
        "kinds": kinds or ["generic"],
        "test_commands": _deduplicate_commands(commands),
        "module_roots": ["modules", "features"],
        "integration_paths": sorted(set(integration)),
        "protected_paths": _protected_files(root),
        "generated_paths": list(GENERATED_PATHS),
        "warnings": warnings,
    }


def validate_project_settings(root: Path, overrides: dict | None = None) -> dict:
    """Apply explicit settings while keeping paths relative and commands argv-only."""
    require(overrides is None or isinstance(overrides, dict), "Project overrides must be an object")
    profile = detect_project(root)
    overrides = overrides or {}
    allowed = {"test_commands", "module_roots", "integration_paths"}
    require(set(overrides) <= allowed, "Unknown project setting")
    if "test_commands" in overrides:
        profile["test_commands"] = _commands(overrides["test_commands"])
    for key in ("module_roots", "integration_paths"):
        if key in overrides:
            profile[key] = _relative_paths(overrides[key], key)
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
