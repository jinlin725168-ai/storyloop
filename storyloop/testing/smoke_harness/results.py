"""Strict execution evidence; unknown outcomes never prove RED or GREEN."""
from dataclasses import dataclass
import json
import re

from ...models import require
from ...processutil import redact

PREFIX = "STORYLOOP_HARNESS_V1="
COUNTS = ("executed", "failed", "errors", "skipped")
STAGES = {"launch", "input", "source_sync", "dependency_cache", "build", "start", "ready",
          "run_case", "runner", "import", "timeout", "cleanup", "service_exit", "service_cleanup",
          "precondition"}
CASE_DIAGNOSTIC_KEYS = {"case_id", "kind", "message", "traceback_tail"}


def _valid_diagnostic(item: object) -> bool:
    if isinstance(item, str):
        return item in STAGES or bool(re.fullmatch(
            r"scratch_cleanup(?::storyloop-[0-9a-f]+-[A-Za-z0-9_-]+)?", item))
    return (isinstance(item, dict) and set(item) == CASE_DIAGNOSTIC_KEYS
            and item.get("kind") in ("error", "fail")
            and all(isinstance(item[key], str) for key in CASE_DIAGNOSTIC_KEYS)
            and 0 < len(item["case_id"]) <= 300 and 0 < len(item["message"]) <= 2000
            and len(item["traceback_tail"]) <= 4000)


@dataclass(frozen=True)
class Result:
    """Actual counts and sanitized stage identifiers; errors dominate all failures."""
    executed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    diagnostics: tuple[str | dict, ...] = ()

    def __post_init__(self):
        require(all(type(getattr(self, key)) is int and getattr(self, key) >= 0 for key in COUNTS),
                "Invalid harness counts")
        require(self.failed <= self.executed, "Failures exceed executed tests")
        require(isinstance(self.diagnostics, tuple) and len(self.diagnostics) <= 20
                and all(_valid_diagnostic(item) for item in self.diagnostics),
                "Invalid harness diagnostic")

    @property
    def code(self) -> int:
        """0: executed and green; 1: assertions failed; 2: environment or no execution."""
        return 2 if self.errors or not self.executed else (1 if self.failed else 0)

    def __add__(self, other):
        require(isinstance(other, Result), "Unrecognized runner result")
        diagnostics = list(self.diagnostics)
        diagnostics.extend(item for item in other.diagnostics if item not in diagnostics)
        return Result(**{key: getattr(self, key) + getattr(other, key) for key in COUNTS},
                      diagnostics=tuple(diagnostics[:20]))

    def document(self) -> dict:
        """Stable machine evidence with bounded, sanitized case failure details."""
        diagnostics = [({key: redact(value) if isinstance(value, str) else value
                         for key, value in item.items()} if isinstance(item, dict) else item)
                       for item in self.diagnostics]
        return {**{key: getattr(self, key) for key in COUNTS}, "code": self.code,
                "diagnostics": diagnostics}


def decode(value: object) -> Result:
    """Validate an entire launcher record rather than trusting an arbitrary code."""
    require(isinstance(value, dict) and set(value) == {*COUNTS, "code", "diagnostics"}
            and isinstance(value["diagnostics"], list), "Invalid harness result")
    result = Result(**{key: value[key] for key in COUNTS}, diagnostics=tuple(value["diagnostics"]))
    require(type(value["code"]) is int and value["code"] == result.code, "Inconsistent harness result")
    return result


def evidence(stdout: str, returncode: int | None) -> Result:
    """Consume only the final launcher record, never child runner text."""
    try:
        line = stdout.strip().splitlines()[-1]
        require(line.startswith(PREFIX), "Missing harness evidence")
        result = decode(json.loads(line[len(PREFIX):]))
        require(returncode == result.code, "Harness exit disagrees with evidence")
        return result
    except (ValueError, IndexError, TypeError):
        return Result(errors=1, diagnostics=("runner",))
