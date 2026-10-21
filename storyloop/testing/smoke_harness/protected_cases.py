"""Pin protected case bytes before hooks; candidate code cannot replace the judge mid-run."""
from contextlib import contextmanager
from contextvars import ContextVar
import os
from pathlib import Path
import sys

from ...models import require
from ...path_security import safe_target
from .cases import validate_cases

_PINNED = ContextVar("storyloop_protected_cases", default=None)


def _trusted_imports(project: Path) -> None:
    for value in sys.path:
        entry = Path(value or os.getcwd()).resolve()
        require(not (entry == project or project in entry.parents or entry in project.parents),
                "Candidate paths cannot participate in trusted test imports; use the isolated launcher")


@contextmanager
def protected_cases(project: Path, cases: list[str]):
    """Freeze relative import roots before any hook or case can change the cwd."""
    previous = list(sys.path)
    sys.path[:] = [str(Path(entry or os.getcwd()).absolute()) for entry in previous]
    try:
        with _pin_cases(project, cases):
            yield
    finally:
        sys.path[:] = previous


@contextmanager
def _pin_cases(project: Path, cases: list[str]):
    """Nested invocations may only select the same already-pinned project and cases."""
    project = Path(project).resolve()
    _trusted_imports(project)
    selected = validate_cases(project, cases)
    current = _PINNED.get()
    if current is not None:
        require(current[0] == project and all(path in current[1] for path in selected),
                "Case was not selected by the protected invocation")
        yield
        return
    contents = {relative: safe_target(project, relative, "protected case").read_bytes()
                for relative in selected}
    token = _PINNED.set((project, contents))
    try:
        yield
    finally:
        _PINNED.reset(token)


def case_bytes(project: Path, relative: str) -> bytes:
    """Return pinned bytes only while the file still matches its protected input."""
    current = _PINNED.get()
    require(current is not None and current[0] == project and relative in current[1],
            "Missing protected case snapshot")
    content = current[1][relative]
    require(safe_target(project, relative, "protected case").read_bytes() == content,
            "Protected case changed during execution")
    return content


@contextmanager
def case_directory(cwd: Path | None):
    previous = Path.cwd()
    try:
        if cwd is not None:
            os.chdir(cwd)
        yield
    finally:
        if cwd is not None:
            os.chdir(previous)
