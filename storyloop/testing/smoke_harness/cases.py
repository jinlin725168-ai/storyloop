"""Exact, bounded case selection with no discovery or recursive source scanning."""
import os
from pathlib import Path

from ...models import require
from ...path_security import repository_relative, safe_target


def validate_cases(project: Path, cases: list[str], roots: list[str] | None = None) -> list[str]:
    """Return normalized Python file paths, rejecting aliases, links and out-of-scope files."""
    require(isinstance(cases, (list, tuple)) and 0 < len(cases) <= 200,
            "Explicit nonempty case list required")
    allowed = [repository_relative(root, "case root") for root in roots] if roots else None
    result, seen = [], set()
    for item in cases:
        relative = repository_relative(item, "case")
        require(not allowed or any(relative.startswith(root + "/") for root in allowed),
                "Case is outside declared roots")
        target = safe_target(project, relative, "case")
        key = os.path.normcase(str(target))
        require(key not in seen and target.is_file() and target.suffix == ".py",
                "Duplicate, missing or unsupported case")
        require(target.stat().st_size <= 512_000, "Case is too large")
        seen.add(key)
        result.append(relative)
    return result
