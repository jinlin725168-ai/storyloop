"""Strict, dependency-free contracts for stage-oriented Loop configuration."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping

from .models import StoryLoopError, fields, identifier, require, strings, text


_ENVIRONMENT_NAME = re.compile(r"[A-Z_][A-Z0-9_]{0,63}")
_PATH_COMPONENT = re.compile(r"(?:[A-Za-z0-9._-]+|\*|\*\*)")
_SENSITIVE_NAMES = ("KEY", "TOKEN", "PASSWORD", "SECRET", "CREDENTIAL")
_INLINE_SECRET = re.compile(r"(?:api[_-]?key|token|password|secret|credential)\s*=", re.IGNORECASE)
_RESERVED_PATHS = {".git", ".storyloop"}
_PHASES = {"red", "l1", "l2", "final"}


def validate_loop_config(value: object) -> dict:
    """Validate a complete v2 configuration without mutating or normalizing it."""
    fields(value, {"version", "agent", "budgets", "stages"})
    require(value["version"] == 2, "Unsupported loop config version")
    _validate_agent(value["agent"])
    _validate_budgets(value["budgets"])
    _validate_stages(value["stages"])
    _ordered_stages(value["stages"])
    return value


def select_adapter(config: dict, requested: str | None = None,
                   environ: Mapping[str, str] | None = None) -> tuple[str, dict]:
    """Resolve CLI, environment and default adapter selection in fixed order."""
    validate_loop_config(config)
    source = os.environ if environ is None else environ
    environment_name = source.get("LOOP_AGENT")
    selected = requested if requested is not None else environment_name or config["agent"]["default"]
    require(isinstance(selected, str), "Selected adapter must be text")
    adapters = config["agent"]["adapters"]
    require(selected in adapters, "Unknown selected adapter")
    return selected, adapters[selected]


def stage_order(config: dict) -> list[dict]:
    """Return a stable dependency order, preserving declaration order for ties."""
    validate_loop_config(config)
    return _ordered_stages(config["stages"])


def _validate_agent(value: object) -> None:
    fields(value, {"default", "adapters", "timeout_seconds", "environment_allowlist"})
    default = identifier(value["default"], "default adapter")
    require(isinstance(value["adapters"], dict) and bool(value["adapters"]),
            "adapters must be a nonempty object")
    require(len(value["adapters"]) <= 20, "Too many adapters")
    for name, adapter in value["adapters"].items():
        identifier(name, "adapter id")
        _validate_adapter(adapter)
    require(default in value["adapters"], "Default adapter is not registered")
    _bounded_integer(value["timeout_seconds"], "agent timeout_seconds", 1, 86400)
    _validate_environment(value["environment_allowlist"])


def _validate_adapter(value: object) -> None:
    fields(value, {"kind", "command"})
    require(value["kind"] in {"codex", "command", "manual"}, "Unknown adapter kind")
    command = strings(value["command"], "adapter command")
    require(len(command) <= 100, "Adapter command is too long")
    if value["kind"] == "command":
        require(bool(command), "Command adapter needs explicit argv")
    else:
        require(not command, "Built-in adapters cannot define a command")
    require(not any(_INLINE_SECRET.search(argument) for argument in command),
            "Adapter command cannot contain inline sensitive values")


def _validate_environment(value: object) -> None:
    names = strings(value, "environment allowlist")
    require(len(names) <= 64, "Environment allowlist is too large")
    require(len(set(names)) == len(names), "Duplicate environment name")
    for name in names:
        require(bool(_ENVIRONMENT_NAME.fullmatch(name)), "Invalid environment name")
        require(not any(marker in name for marker in _SENSITIVE_NAMES),
                "Sensitive environment variables cannot be forwarded")


def _validate_budgets(value: object) -> None:
    names = {"attempts_per_stage", "total_seconds", "repeated_failure_limit", "backoff_seconds"}
    fields(value, names)
    attempts = _bounded_integer(value["attempts_per_stage"], "attempts_per_stage", 1, 100)
    _bounded_integer(value["total_seconds"], "total_seconds", 1, 86400)
    _bounded_integer(value["repeated_failure_limit"], "repeated_failure_limit", 1, attempts)
    backoff = value["backoff_seconds"]
    require(isinstance(backoff, list), "backoff_seconds must be a list")
    require(len(backoff) < attempts, "backoff_seconds needs fewer entries than attempts_per_stage")
    for delay in backoff:
        _bounded_integer(delay, "backoff_seconds", 0, 3600)
    require(backoff == sorted(backoff), "backoff_seconds must be nondecreasing")


def _validate_stages(value: object) -> None:
    require(isinstance(value, list) and bool(value), "stages must be a nonempty list")
    require(len(value) <= 100, "Too many stages")
    seen: set[str] = set()
    for stage in value:
        key = _validate_stage(stage)
        require(key not in seen, "Duplicate stage id")
        seen.add(key)
    for stage in value:
        unknown = set(stage["depends_on"]) - seen
        require(not unknown, "Unknown stage dependency")


def _validate_stage(value: object) -> str:
    names = {"id", "depends_on", "specs", "write_paths", "protected_paths", "judges"}
    fields(value, names)
    key = identifier(value["id"], "stage id")
    dependencies = strings(value["depends_on"], "stage dependency")
    for dependency in dependencies:
        identifier(dependency, "stage dependency")
    require(len(dependencies) == len(set(dependencies)), "Duplicate stage dependency")
    specs = _validate_specs(value["specs"])
    writes = _validate_patterns(value["write_paths"], "write path")
    protected = _validate_patterns(value["protected_paths"], "protected path")
    require(all(any(_patterns_overlap(spec, pattern) for pattern in protected) for spec in specs),
            "Spec paths must be protected")
    require(not _collections_overlap(writes, protected), "Write and protected paths overlap")
    _validate_judges(value["judges"])
    return key


def _validate_specs(value: object) -> list[str]:
    specs = strings(value, "spec path")
    require(bool(specs) and len(specs) <= 100, "specs must be a nonempty bounded list")
    require(len(specs) == len(set(specs)), "Duplicate spec path")
    for path in specs:
        _portable_pattern(path, "spec path")
        require("*" not in path, "Spec paths cannot contain wildcards")
        require(path.lower().endswith(".md"), "Spec paths must reference Markdown files")
    return specs


def _validate_patterns(value: object, label: str) -> list[str]:
    patterns = strings(value, label)
    require(bool(patterns) and len(patterns) <= 100, f"{label}s must be a nonempty bounded list")
    for pattern in patterns:
        _portable_pattern(pattern, label)
    require(not _collections_overlap(patterns, patterns, distinct=True), f"{label} patterns overlap")
    return patterns


def _portable_pattern(value: str, label: str) -> None:
    text(value, label)
    require(len(value) <= 500 and "\\" not in value and ":" not in value,
            f"Invalid {label}: use a portable relative path")
    require(not value.startswith("/"), f"Invalid {label}: absolute paths are forbidden")
    parts = value.split("/")
    require(all(parts) and all(part and _PATH_COMPONENT.fullmatch(part) for part in parts),
            f"Invalid {label}: unsupported path component")
    require(not any(part in {".", ".."} or part.casefold() in _RESERVED_PATHS for part in parts),
            f"Invalid {label}: reserved or traversing path")
    require("*" not in parts[0], f"Invalid {label}: a literal top-level boundary is required")


def _validate_judges(value: object) -> None:
    require(isinstance(value, list) and bool(value), "judges must be a nonempty list")
    require(len(value) <= 100, "Too many judges")
    seen: set[str] = set()
    phases: list[str] = []
    for judge in value:
        fields(judge, {"id", "phase", "command"})
        key = identifier(judge["id"], "judge id")
        require(key not in seen, "Duplicate judge id")
        seen.add(key)
        require(judge["phase"] in _PHASES, "Unknown judge phase")
        phases.append(judge["phase"])
        command = strings(judge["command"], "judge command")
        require(bool(command) and len(command) <= 100, "Judge command needs bounded argv")
    require(phases.count("red") == 1, "A stage needs exactly one red judge")
    require("l1" in phases, "A stage needs an l1 judge")
    require("l2" in phases, "A stage needs an l2 judge")


def _ordered_stages(stages: list[dict]) -> list[dict]:
    remaining = {stage["id"]: set(stage["depends_on"]) for stage in stages}
    ordered: list[dict] = []
    while remaining:
        ready = [stage for stage in stages if stage["id"] in remaining and not remaining[stage["id"]]]
        require(bool(ready), "Stage dependency cycle detected")
        for stage in ready:
            ordered.append(stage)
            remaining.pop(stage["id"])
            for dependencies in remaining.values():
                dependencies.discard(stage["id"])
    return ordered


def _collections_overlap(first: list[str], second: list[str], distinct: bool = False) -> bool:
    for left_index, left in enumerate(first):
        for right_index, right in enumerate(second):
            if distinct and left_index >= right_index:
                continue
            if _patterns_overlap(left, right):
                return True
    return False


def _patterns_overlap(left: str, right: str) -> bool:
    left_prefix, right_prefix = _literal_prefix(left), _literal_prefix(right)
    return (left_prefix == right_prefix[:len(left_prefix)]
            or right_prefix == left_prefix[:len(right_prefix)])


def _literal_prefix(pattern: str) -> tuple[str, ...]:
    parts = pattern.split("/")
    wildcard = next((index for index, part in enumerate(parts) if "*" in part), len(parts))
    return tuple(part.casefold() for part in parts[:wildcard])


def _bounded_integer(value: object, label: str, minimum: int, maximum: int) -> int:
    require(type(value) is int and minimum <= value <= maximum, f"Invalid {label}")
    return value


