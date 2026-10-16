"""Safe validation of repository evidence locations in a candidate or contract commit."""

from __future__ import annotations

import re
from pathlib import Path

from .baseline import _git
from .failure_attribution import location_file
from .models import StoryLoopError
from .path_security import safe_target

LOCATION = re.compile(r"^(?P<path>[^:]+):(?P<line>[1-9][0-9]*)(?::[1-9][0-9]*)?$")


def evidence_location_exists(project: Path, location: object, contract_commit: str | None) -> bool:
    """Return whether `file:line[:column]` names a safe real line in either allowed tree."""
    parsed = _parse(location)
    if parsed is None:
        return False
    path, line = parsed
    return _candidate_has_line(Path(project).resolve(), path, line) or bool(
        contract_commit and _commit_has_line(Path(project).resolve(), contract_commit, path, line))


def _parse(location: object) -> tuple[str, int] | None:
    if not isinstance(location, str):
        return None
    normalized = location.strip().replace("\\", "/")
    match = LOCATION.fullmatch(normalized)
    path = location_file(normalized)
    if not match or path is None:
        return None
    return path, int(match.group("line"))


def _candidate_has_line(project: Path, relative: str, line: int) -> bool:
    try:
        path = safe_target(project, relative, "evidence location")
        return path.is_file() and _has_line(path.read_text(encoding="utf-8", errors="replace"), line)
    except (OSError, ValueError, StoryLoopError):
        return False


def _commit_has_line(project: Path, commit: str, relative: str, line: int) -> bool:
    try:
        entry = _git(project, "ls-tree", commit, "--", relative).strip()
        if not entry or entry.split(None, 1)[0] not in {"100644", "100755"}:
            return False
        content = _git(project, "show", f"{commit}:{relative}")
    except (OSError, StoryLoopError):
        return False
    return _has_line(content, line)


def _has_line(content: str, line: int) -> bool:
    return line <= len(content.splitlines())
