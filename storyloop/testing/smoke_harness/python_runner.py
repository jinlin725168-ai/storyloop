"""Run selected Python cases in disposable, contained business processes."""
import json
from pathlib import Path
import sys
import tempfile

from ...models import StoryLoopError
from ...processutil import run_process
from .cases import validate_cases
from .results import Result, decode

ENVIRONMENT = ["PATH", "SYSTEMROOT", "TEMP", "TMP", "PYTHONDONTWRITEBYTECODE"]


def python_cases(project: Path, cases: list[str], timeout: float = 60) -> Result:
    """Run only the named unittest files; assertion failures=1, import/runtime/zero tests=2."""
    try:
        paths = validate_cases(project, cases)
        result = Result()
        for relative in paths:
            result += _one_case(Path(project).resolve(), relative, timeout)
        return result
    except (StoryLoopError, OSError, ValueError):
        return Result(errors=1)


def _one_case(project: Path, relative: str, timeout: float) -> Result:
    with tempfile.TemporaryDirectory(prefix="storyloop-case-") as folder:
        record = Path(folder) / "result.json"
        argv = [str(Path(sys.executable).absolute()), "-I", "-B",
                str(Path(__file__).with_name("worker.py").resolve()), str(project), relative, str(record)]
        outcome = run_process(argv, project, timeout, environment_allowlist=ENVIRONMENT)
        if (not outcome.get("contained") or outcome.get("timed_out")
                or outcome.get("truncated") or outcome.get("error")):
            return Result(errors=1)
        try:
            result = decode(json.loads(record.read_text(encoding="utf-8")))
            if result.code != outcome["returncode"] or not result.executed:
                return result + Result(errors=1)
            return result
        except (OSError, ValueError, StoryLoopError):
            return Result(errors=1)
