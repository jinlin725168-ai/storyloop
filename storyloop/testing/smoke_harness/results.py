"""Strict execution evidence; unknown outcomes never prove RED or GREEN."""
from dataclasses import asdict, dataclass
import json

from ...models import require

PREFIX = "STORYLOOP_HARNESS_V1="


@dataclass(frozen=True)
class Result:
    """Actual test counts; errors dominate assertion failures and zero execution."""
    executed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0

    def __post_init__(self):
        require(all(type(value) is int and value >= 0 for value in asdict(self).values()),
                "Invalid harness counts")
        require(self.failed <= self.executed, "Failures exceed executed tests")

    @property
    def code(self) -> int:
        """0: executed and green; 1: assertions failed; 2: environment or no execution."""
        return 2 if self.errors or not self.executed else (1 if self.failed else 0)

    def __add__(self, other):
        require(isinstance(other, Result), "Unrecognized runner result")
        return Result(*(getattr(self, key) + getattr(other, key) for key in asdict(self)))

    def document(self) -> dict:
        """Stable machine evidence, including the exit code implied by the counts."""
        return {**asdict(self), "code": self.code}


def decode(value: object) -> Result:
    """Validate an entire worker evidence object rather than trusting an arbitrary code."""
    require(isinstance(value, dict) and set(value) ==
            {"executed", "failed", "errors", "skipped", "code"}, "Invalid harness result")
    result = Result(**{key: value[key] for key in ("executed", "failed", "errors", "skipped")})
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
        return Result(errors=1)
