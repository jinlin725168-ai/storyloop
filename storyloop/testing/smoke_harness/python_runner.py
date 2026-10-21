"""Run protected unittest files in the trusted judge, never import candidate business code."""
import contextlib
import types
import os
from pathlib import Path
import time
import unittest

from ...models import StoryLoopError, require
from .cases import validate_cases
from .results import Result
from .protected_cases import case_bytes, case_directory, protected_cases

def python_cases(project: Path, cases: list[str], timeout: float = 60, *, cwd: Path | None = None) -> Result:
    """Measure protected tests directly; business code must run in separate subprocesses.

    No candidate directory is added to sys.path. Importing candidate modules fails closed.
    The timeout is a cooperative suite budget; manifest timeout_seconds supplies the hard
    cross-platform process deadline. There is no writable worker evidence file or channel.
    """
    try:
        require(type(timeout) in {int, float} and timeout > 0, "Positive case timeout required")
        paths = validate_cases(project, cases)
        with protected_cases(project, paths), case_directory(cwd):
            return _selected(Path(project).resolve(), paths, time.monotonic() + timeout)
    except (StoryLoopError, OSError, ValueError):
        return Result(errors=1, diagnostics=("runner",))


def _selected(project: Path, paths: list[str], deadline: float) -> Result:
    result = Result()
    for relative in paths:
        if time.monotonic() >= deadline:
            return result + Result(errors=1)
        result += _one_case(project, relative)
    return result + Result(errors=int(time.monotonic() > deadline))


def _one_case(project: Path, relative: str) -> Result:
    try:
        source = case_bytes(project, relative)
        module = types.ModuleType("storyloop_protected_case")
        module.__file__ = str(project / relative)
        with open(os.devnull, "w", encoding="utf-8") as sink, \
                contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            exec(compile(source, str(project / relative), "exec"), module.__dict__)
            suite = unittest.defaultTestLoader.loadTestsFromModule(module)
            result = unittest.TestResult()
            suite.run(result)
        skipped = len(result.skipped) + len(result.expectedFailures)
        counts = Result(executed=result.testsRun - skipped, failed=len(result.failures),
                        errors=len(result.errors) + len(result.unexpectedSuccesses), skipped=skipped)
        return counts if counts.executed else counts + Result(errors=1)
    except BaseException:
        return Result(errors=1, diagnostics=("runner",))
