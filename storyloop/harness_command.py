"""Resolve the portable harness token using the trusted running installation."""
from pathlib import Path
import sys

from .models import StoryLoopError


def expand_command(command: list[str]) -> list[str]:
    """Expand only the first harness token; preserve legacy per-argument {python}."""
    if command and command[0] == "{storyloop_harness}":
        launcher = Path(__file__).parent / "testing/smoke_harness/launcher.py"
        return [str(Path(sys.executable).absolute()), "-I", "-B", str(launcher.resolve()), *command[1:]]
    return [sys.executable if arg == "{python}" else arg for arg in command]


def harness_counts(outcome: dict) -> dict:
    """Reject missing/contradictory structured evidence for the new protocol only."""
    from .testing.smoke_harness.results import Result, evidence
    try:
        result = evidence(outcome.get("stdout", ""), outcome.get("returncode"))
    except StoryLoopError:
        result = Result(errors=1)
    if result.errors or not result.executed:
        outcome["returncode"] = 2
    return {**result.document(), "parser": "storyloop_harness_v1"}
