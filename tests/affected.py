"""Select the test modules a change can break, from changed paths (standard library only).

A changed test module runs itself; a changed `storyloop/*.py` or test helper runs the test
modules that import it directly; any other file runs the test modules whose source names its
path or its parent directory. Transitive importers are left to the suite this is added to:
measured 2026-11-16, they reach a median of 113 of 236 modules, which is no selection at all.
"""
from __future__ import annotations

import ast
import subprocess
from pathlib import Path


def changed_files(root: Path, base: str) -> list[str]:
    """Paths changed since the merge base with `base`, plus uncommitted edits."""
    committed = _git(root, "diff", "--name-only", f"{base}...HEAD")
    working = _git(root, "diff", "--name-only", "HEAD")
    return sorted({line for line in committed + working if line})


def affected_modules(root: Path, changed: list[str]) -> list[str]:
    tests = {path.stem: path for path in sorted((root / "tests").glob("test_*.py"))}
    imports = {name: _imports(path) for name, path in tests.items()}
    sources = {name: path.read_text(encoding="utf-8", errors="replace")
               for name, path in tests.items()}
    selected: set[str] = set()
    for name in changed:
        path = Path(name)
        if path.parts[:1] == ("tests",) and path.suffix == ".py" and len(path.parts) == 2:
            if path.stem in tests:
                selected.add(path.stem)
                continue
            target = f"tests.{path.stem}"
        elif path.parts[:1] == ("storyloop",) and path.suffix == ".py" and len(path.parts) == 2:
            target = f"storyloop.{path.stem}"
        elif path.parts[:1] == (".github",):
            # Many tests write a `.github/workflows/ci.yml` into temporary repositories; only
            # test_ci_workflow reads this repository's own workflow.
            selected |= {"test_ci_workflow"} & set(tests)
            continue
        else:
            needles = _path_needles(path)
            # test_affected's own fixtures spell example paths; it is selected by its own changes.
            selected |= {test for test, text in sources.items() if test != "test_affected"
                         and any(needle in text for needle in needles)}
            continue
        selected |= {test for test, names in imports.items() if target in names}
    return sorted(f"tests.{name}" for name in selected)


def _imports(path: Path) -> set[str]:
    """Dotted modules a test imports: `from storyloop.x import`, `import storyloop.x`,
    `from storyloop import x`, and the same for `tests.`."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return set()
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
            found |= {f"{node.module}.{alias.name}" for alias in node.names}
        elif isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
    return found


def _path_needles(path: Path) -> set[str]:
    """The path itself, without its top folder, and parent folders at least two levels deep."""
    parts = path.parts
    needles = {path.as_posix()}
    if parts[:1] in (("tests",), ("storyloop",)) and len(parts) > 1:
        inner = parts[1:]
        needles.add("/".join(inner))
        needles |= {"/".join(inner[:end]) for end in range(2, len(inner))}
    # Also the pathlib spelling `"fixtures" / "lifecycle"`.
    return needles | {" / ".join(f'"{part}"' for part in needle.split("/"))
                      for needle in needles if "/" in needle}


def _git(root: Path, *args: str) -> list[str]:
    done = subprocess.run(["git", "-c", "core.quotepath=false", *args], cwd=root,
                          capture_output=True, text=True, encoding="utf-8", check=True)
    return done.stdout.splitlines()
