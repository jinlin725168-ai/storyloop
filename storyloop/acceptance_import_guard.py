"""Reject acceptance tests that import the project inside the test runner process."""

from __future__ import annotations

import ast
from pathlib import Path

from .baseline import load_project_config
from .models import StoryLoopError
from .path_security import safe_target


def check_suite_imports(project: Path, suite: Path) -> None:
    """Check a locked suite before RED, while acceptance-repair is still available."""
    names = _project_names(project)
    for path in sorted(suite.rglob('*.py')):
        relative = path.relative_to(project).as_posix()
        source = safe_target(project, relative, 'acceptance test').read_text(encoding='utf-8')
        _check_source(source, relative, names)


def check_generated_imports(project: Path, files: object) -> None:
    """Reject a repair response before replacing the protected suite."""
    if not isinstance(files, list):
        return  # The existing suite validator reports malformed responses.
    names = _project_names(project)
    for item in files:
        if (isinstance(item, dict) and isinstance(item.get('path'), str)
                and isinstance(item.get('content'), str) and item['path'].endswith('.py')):
            _check_source(item['content'], item['path'], names)


def _check_source(source: str, path: str, names: set[str]) -> None:
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError:
        return  # Syntax errors are judged by the ordinary RED runner.
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            imports = [node.module or '']
        else:
            continue
        for name in imports:
            root = name.split('.', 1)[0]
            if root in names:
                raise StoryLoopError(
                    f'验收用例 {path}:{node.lineno} 在测试进程直接导入项目包 {root}；'
                    '请用 acceptance-repair 修正：在候选镜像 Path.cwd() 中由子进程加载业务代码。',
                    code='ORACLE_DEFECT')


def _project_names(project: Path) -> set[str]:
    profile = load_project_config(project).get('profile', {})
    names = set()
    for declared in (*profile.get('module_roots', []), *profile.get('test_roots', [])):
        parts = Path(declared).parts
        if not parts:
            continue
        names.add(parts[0])  # a root-level directory is also an importable namespace package
    names.update(child.name for child in project.iterdir()
                 if child.is_dir() and (child / '__init__.py').is_file())
    if (project / 'tests').is_dir():
        names.add('tests')
    return names
