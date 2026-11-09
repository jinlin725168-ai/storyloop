"""Detect candidate-side helper imports in StoryLoop self-hosted acceptance cases."""

from __future__ import annotations

import ast
from pathlib import Path

from . import __version__
from .models import StoryLoopError
from .smoke import suite_root


def preflight_self_harness(project: Path, change_id: str, manifest: dict) -> None:
    """Stop before RED or acceptance executes a helper from the candidate package."""
    candidate_helper = project / "storyloop/testing"
    if not candidate_helper.is_dir():
        return
    suite = suite_root(project, change_id)
    offenders: list[str] = []
    for test in manifest["tests"]:
        command = test["command"]
        if command[0] == "{storyloop_harness}":
            continue
        if any(_helper_name(arg) for arg in command[1:]):
            offenders.append(f"manifest:{test['id']}:command")
        if "-c" in command:
            index = command.index("-c")
            if index + 1 < len(command) and _imports_helper(command[index + 1]):
                offenders.append(f"manifest:{test['id']}:inline")
        for path in _command_scripts(project, suite, command):
            try:
                source = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue  # The existing runner will report an unreadable fixture.
            if _imports_helper(source):
                offenders.append(path.relative_to(project).as_posix())
    if offenders:
        engine = Path(__file__).parent / "testing/smoke_harness"
        raise StoryLoopError(
            "自举验收夹具导入了候选镜像的 storyloop.testing；改用 manifest 的 "
            "{storyloop_harness} 入口或引擎提供的 helper，业务代码仍由候选子进程执行",
            code="ORACLE_DEFECT", details={"reason": "HARNESS_SHADOWED",
                                            "files": sorted(set(offenders)),
                                            "candidate_helper": str(candidate_helper),
                                            "engine_helper": str(engine.resolve()),
                                            "engine_version": __version__})


def _command_scripts(project: Path, suite: Path, command: list[str]) -> list[Path]:
    scripts = []
    for arg in command[1:]:
        if not arg.endswith(".py"):
            continue
        path = (project / arg).resolve()
        if path.is_file() and path.is_relative_to(suite.resolve()):
            scripts.append(path)
    return scripts or sorted(
        path for path in suite.rglob("*.py")
        if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(suite.resolve())
    )


def _imports_helper(source: str) -> bool:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False  # Syntax errors remain ordinary fixture environment errors.
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(_helper_name(alias.name) for alias in node.names):
            return True
        if isinstance(node, ast.ImportFrom) and (_helper_name(node.module or "") or
                node.module == "storyloop" and any(alias.name == "testing" for alias in node.names)):
            return True
        if isinstance(node, ast.Call):
            direct = isinstance(node.func, ast.Name) and node.func.id == "__import__"
            importlib_call = (isinstance(node.func, ast.Attribute) and node.func.attr == "import_module"
                              and isinstance(node.func.value, ast.Name) and node.func.value.id == "importlib")
            if (direct or importlib_call) and node.args and isinstance(node.args[0], ast.Constant):
                if _helper_name(node.args[0].value):
                    return True
    return False


def _helper_name(value: object) -> bool:
    return isinstance(value, str) and (value == "storyloop.testing" or value.startswith("storyloop.testing."))
