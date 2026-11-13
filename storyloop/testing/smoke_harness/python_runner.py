"""Run protected unittest files in the trusted judge, never import candidate business code."""
import contextlib
import types
import os
from pathlib import Path
import sys
import time
import traceback
import unittest

from ...models import StoryLoopError, require
from ...processutil import redact
from .cases import validate_cases
from .results import Result
from .protected_cases import case_bytes, case_directory, protected_cases


class FixturePreconditionError(Exception):
    """A protected case could not establish the fixture needed to test behavior."""


def precondition(condition: bool, message: str) -> None:
    """Mark a fixture failure without counting it as a behavior assertion failure."""
    if not condition:
        line = " ".join(str(message)[:1500].split())
        print("STORYLOOP_PRECONDITION_FAILED: " + redact(line)[:1000], file=sys.__stderr__)
        raise FixturePreconditionError(message)


class _CaseResult(unittest.TestResult):
    def __init__(self, project: Path):
        super().__init__()
        self.project = project
        self.precondition_messages = []
        self.case_diagnostics = []

    def addError(self, test, error):
        super().addError(test, error)
        self.case_diagnostics.append(_case_diagnostic(self.project, test.id(), "error", error))
        if isinstance(error[1], FixturePreconditionError):
            self.precondition_messages.append(f"{test.id()}: {error[1]}")

    def addFailure(self, test, error):
        super().addFailure(test, error)
        self.case_diagnostics.append(_case_diagnostic(self.project, test.id(), "fail", error))

    def addSubTest(self, test, subtest, error):
        super().addSubTest(test, subtest, error)
        if error is None:
            return
        kind = "fail" if issubclass(error[0], test.failureException) else "error"
        self.case_diagnostics.append(_case_diagnostic(self.project, subtest.id(), kind, error))
        if isinstance(error[1], FixturePreconditionError):
            self.precondition_messages.append(f"{subtest.id()}: {error[1]}")


def _case_diagnostic(project: Path, case_id: str, kind: str, error: tuple) -> dict:
    frames = []
    for frame in traceback.extract_tb(error[2]):
        try:
            relative = Path(frame.filename).resolve().relative_to(project)
        except ValueError:
            continue
        frames.append(f"{relative.as_posix()}:{frame.lineno} in {frame.name}")
    message = f"{error[0].__name__}: {error[1]}"
    return {"case_id": redact(case_id)[:300], "kind": kind,
            "message": redact(message)[:2000],
            "traceback_tail": redact("\n".join(frames))[-4000:]}


def _method_id(test) -> str:
    """unittest stores each failed subTest separately, but testsRun counts methods."""
    parent = getattr(test, "test_case", None)
    return (parent if isinstance(parent, unittest.TestCase) else test).id()

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
            result = _CaseResult(project)
            suite.run(result)
        if result.precondition_messages:
            summary = "\n".join(result.precondition_messages[-3:])[-1500:]
            print(redact(summary), file=sys.__stderr__)
        skipped = len(result.skipped) + len(result.expectedFailures)
        errored = {_method_id(test) for test, _ in result.errors}
        errored.update(_method_id(test) for test in result.unexpectedSuccesses)
        failed = {_method_id(test) for test, _ in result.failures} - errored
        counts = Result(executed=result.testsRun - skipped, failed=len(failed),
                        errors=len(errored), skipped=skipped,
                        diagnostics=(("precondition",) if result.precondition_messages
                                     and len(result.precondition_messages) == len(result.errors)
                                     and not result.unexpectedSuccesses else ())
                        + tuple(result.case_diagnostics[:19]))
        return counts if counts.executed else counts + Result(errors=1)
    except BaseException:
        return Result(errors=1, diagnostics=("runner", _case_diagnostic(
            project, relative, "error", sys.exc_info())))
