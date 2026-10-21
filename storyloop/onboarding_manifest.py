"""Validate onboarding declarations without executing project hooks or reading secrets."""
import ast
import re
from pathlib import Path

from . import __version__
from .models import require
from .onboarding_templates import GUIDE, HINTS, HOOKS, MANIFEST, SETTINGS
from .path_security import repository_relative, safe_target
from .project_profile import GENERATED_PATHS, matches_test_root, validate_project_settings
from .storage import load_json


def load_manifest(project: Path) -> dict:
    """Load a bounded, supported manifest; all reads are explicit controlled paths."""
    path = safe_target(project, MANIFEST, "onboarding manifest")
    require(path.is_file() and path.stat().st_size <= 512_000, "Missing or oversized onboarding manifest")
    value = load_json(path)
    require(value.get("version") == 1 and value.get("template_version") == 1,
            "Unsupported onboarding template version")
    require(value.get("engine_version") == __version__ and type(value.get("helper_api")) is int
            and value["helper_api"] == 1, "Unsupported helper version; install the pinned engine offline")
    stacks = value.get("stacks")
    require(isinstance(stacks, list) and stacks and all(isinstance(s, str) and s in HINTS for s in stacks)
            and len(stacks) == len(set(stacks)), "Invalid onboarding stacks")
    require(isinstance(value.get("executors"), dict) and set(value["executors"]) ==
            {f"acceptance/tools/{stack}_smoke.py" for stack in stacks}, "Invalid protected executors")
    require(isinstance(value.get("case_roots"), list) and value["case_roots"], "Explicit case roots required")
    for root in value["case_roots"]:
        safe_target(project, root, "case root", target_directory=True)
    return value


def validate_hooks(executor: dict) -> None:
    """Every phase is implemented or explicitly inapplicable; cases always execute."""
    require(isinstance(executor, dict), "Invalid executor declaration")
    hooks = executor.get("hooks")
    require(isinstance(hooks, dict) and set(hooks) == set(HOOKS), "Incomplete hook declarations")
    for name, hook in hooks.items():
        require(isinstance(hook, dict), "Invalid hook declaration")
        state = hook.get("status")
        require(state in {"implemented", "not_applicable"}, f"Hook pending: {name}")
        if state == "not_applicable":
            require(name != "run_case" and isinstance(hook.get("reason"), str)
                    and bool(hook["reason"].strip()), "Inapplicable hook needs a reason; run_case is required")
    require(isinstance(executor.get("source_files"), list)
            and isinstance(executor.get("dependency_caches"), list), "Explicit source/cache declarations required")


def validate_onboarding(project: Path, profile: dict) -> dict | None:
    """Legacy projects return None; generated projects must be complete and protected."""
    if not (project / MANIFEST).exists() and not (project / MANIFEST).is_symlink():
        return None
    value = load_manifest(project)
    require(value.get("reviewed") == {"settings": True, "guide": True}, "Onboarding review is incomplete")
    expected = {GUIDE, SETTINGS, *value["executors"]}
    digests = value.get("generated")
    require(isinstance(digests, dict) and set(digests) == expected and all(
        isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest) for digest in digests.values()),
        "Invalid generated file inventory")
    _protected_files(project, profile, expected | {MANIFEST})
    settings = load_json(safe_target(project, SETTINGS))
    recorded = validate_project_settings(project, settings)
    require(all(profile.get(key) == recorded[key] for key in settings),
            "Initialization settings differ from reviewed onboarding settings")
    require(bool(profile["unit_commands"]) and bool(profile["check_commands"])
            and bool(profile["module_roots"]) and bool(profile["test_roots"]),
            "Onboarding requires complete unit/check commands and module/test roots")
    for relative, executor in value["executors"].items():
        validate_hooks(executor)
        _implemented_functions(project, relative, executor)
    profile["protected_paths"] = sorted(set(profile["protected_paths"]) | expected | {MANIFEST})
    return value


def _protected_files(project: Path, profile: dict, expected: set[str]) -> None:
    roots = [*profile["module_roots"], *profile.get("generated_paths", GENERATED_PATHS)]
    for relative in expected:
        path = safe_target(project, relative, "protected onboarding file")
        require(path.is_file() and path.stat().st_size <= 512_000, "Missing or oversized onboarding artifact")
        require(not any(_overlap(relative, root) for root in roots)
                and not matches_test_root(relative, profile["test_roots"]),
                "Onboarding artifacts must stay protected without mutable/test/generated overlap")


def _overlap(path: str, root: str) -> bool:
    left, right = path.casefold(), root.rstrip("/").casefold()
    return left == right or left.startswith(right + "/") or right.startswith(left + "/")


def _implemented_functions(project: Path, relative: str, executor: dict) -> None:
    tree = ast.parse(safe_target(project, relative).read_text(encoding="utf-8-sig"))
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    required = {name for name, hook in executor["hooks"].items() if hook["status"] == "implemented"} | {"run"}
    for name in required:
        node = functions.get(name)
        require(node is not None, f"Missing implemented hook: {name}")
        substantive = [item for item in node.body if not isinstance(item, (ast.Pass, ast.Raise))
                       and not (isinstance(item, ast.Expr) and isinstance(item.value, ast.Constant))
                       and not (isinstance(item, ast.Return) and
                                (item.value is None or isinstance(item.value, ast.Constant)))]
        require(bool(substantive), f"Hook is still a placeholder: {name}")
