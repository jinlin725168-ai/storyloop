"""Packaged, deterministic onboarding drafts; no command or dependency installation."""
import hashlib
from importlib.resources import files
import json

from . import __version__
from .state_format import GUIDE, HOOKS, MANIFEST, ONBOARDING_STACKS, SETTINGS

HINTS = dict(zip(ONBOARDING_STACKS, (
    "确认 reactor/module 与 Surefire/Failsafe，离线使用预热依赖；禁用跳测开关。",
    "确认包管理器与真实 unit/check runner，预热依赖；Windows 使用原生解释器。",
    "确认 unittest/pytest 实际执行数；业务依赖在子进程中加载。",
    "仅通用骨架，无 Gradle/未知栈特化实现；所有阶段由项目明确。",
)))


def json_bytes(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def render(stacks: list[str]) -> dict[str, bytes]:
    """Render the entire draft in memory from bundled resources."""
    resources = files("storyloop").joinpath("templates/onboarding")
    template = resources.joinpath("executor.py.tmpl").read_text(encoding="utf-8")
    artifacts = {f"acceptance/tools/{stack}_smoke.py":
                 template.replace("__STACK__", stack).encode("utf-8") for stack in stacks}
    artifacts[GUIDE] = resources.joinpath("AGENT_GUIDE.md").read_text(encoding="utf-8").encode("utf-8")
    artifacts[SETTINGS] = json_bytes({
        "unit_commands": [], "integration_commands": [], "check_commands": [],
        "base_build_commands": [],
        "module_roots": [], "test_roots": [], "integration_paths": [],
        "candidate_allow": [],
        "scratch_paths": (["__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache"]
                          if "python" in stacks else []),
        "stacks": stacks})
    manifest = {
        "version": 1, "template_version": 1, "engine_version": __version__, "helper_api": 1,
        "stacks": stacks, "reviewed": {"settings": False, "guide": False},
        "case_roots": ["acceptance/changes", "acceptance/smoke"],
        "generated": {path: hashlib.sha256(content).hexdigest() for path, content in artifacts.items()},
        "executors": {f"acceptance/tools/{stack}_smoke.py": {
            "stack": stack, "source_files": [], "dependency_caches": [],
            "hooks": {hook: {"status": "pending"} for hook in HOOKS}}
            for stack in stacks}}
    artifacts[MANIFEST] = json_bytes(manifest)
    return artifacts
