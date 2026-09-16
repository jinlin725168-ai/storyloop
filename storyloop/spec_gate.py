"""Read-only, bounded validation for OpenSpec Markdown proposals."""

from __future__ import annotations

import hashlib
import os
import re
import stat
from pathlib import Path

from .models import StoryLoopError, require
from .safe_path import project_file

MAX_PROPOSAL_BYTES = 1024 * 1024
HEADINGS = (
    "Why", "Spec Delta", "Acceptance Criteria", "Scope", "Test Mapping",
    "Out of Scope",
)
AC_LINE = re.compile(
    r"^\s*-\s*(AC-[A-Za-z0-9_-]+):\s*(When|While|Where|If)\b.+"
    r",\s*the system shall\s+.+$", re.IGNORECASE,
)


def validate_proposal(project: Path, relative_path: str) -> dict:
    """Validate proposal structure, EARS criteria and exact AC/test coverage."""
    raw = read_bounded_markdown(project, relative_path, MAX_PROPOSAL_BYTES)
    content = raw.decode("utf-8-sig")
    require(bool(re.search(r"(?m)^# Change:\s*\S", content)),
            "Proposal needs a Change title")
    sections = _sections(content)
    acceptance_ids = _acceptance_ids(sections["Acceptance Criteria"])
    mapping = _test_mapping(sections["Test Mapping"])
    mapped = [item["ac"] for item in mapping]
    require(len(mapped) == len(set(mapped)), "Duplicate AC test mapping")
    require(set(mapped) == set(acceptance_ids) and len(mapped) == len(acceptance_ids),
            "Every acceptance criterion needs exactly one test mapping")
    return {"path": relative_path,
            "digest": hashlib.sha256(raw).hexdigest(),
            "acceptance_ids": acceptance_ids, "test_mapping": mapping}


def read_bounded_markdown(project: Path, relative_path: str,
                          maximum: int) -> bytes:
    """Read one safe project-relative regular Markdown file without links."""
    require(isinstance(relative_path, str) and relative_path.endswith(".md"),
            "Proposal path must be relative Markdown")
    path = project_file(project, relative_path, 'Proposal')
    try:
        status = path.lstat()
        reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        require(stat.S_ISREG(status.st_mode) and status.st_nlink == 1
                and not getattr(status, "st_file_attributes", 0) & reparse,
                "Proposal must be a regular file")
        require(status.st_size <= maximum, "Proposal exceeds size limit")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as handle:
            current = os.fstat(handle.fileno())
            require((status.st_dev, status.st_ino) == (current.st_dev, current.st_ino),
                    "Input file changed while opening")
            raw = handle.read(maximum + 1)
        require(len(raw) <= maximum, "Proposal exceeds size limit")
        raw.decode("utf-8-sig")
        return raw
    except StoryLoopError:
        raise
    except (OSError, UnicodeError) as exc:
        raise StoryLoopError("Cannot read bounded proposal") from exc


def _sections(content: str) -> dict[str, str]:
    positions = []
    for heading in HEADINGS:
        match = re.search(rf"(?m)^## {re.escape(heading)}(?:\s|（|$)", content)
        require(match is not None, f"Proposal needs {heading} section")
        positions.append((heading, match.start(), match.end()))
    require([item[1] for item in positions] == sorted(item[1] for item in positions),
            "Proposal sections are out of order")
    result = {}
    for index, (heading, _, start) in enumerate(positions):
        end = positions[index + 1][1] if index + 1 < len(positions) else len(content)
        result[heading] = content[start:end]
    return result


def _acceptance_ids(section: str) -> list[str]:
    result = []
    for line in section.splitlines():
        if line.lstrip().startswith("- AC-"):
            match = AC_LINE.fullmatch(line)
            require(match is not None, "Acceptance criterion must use EARS format")
            result.append(match.group(1))
    require(bool(result), "Proposal needs acceptance criteria")
    require(len(result) == len(set(result)), "Duplicate acceptance criterion id")
    return result


def _test_mapping(section: str) -> list[dict]:
    result = []
    for line in section.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 3 or cells[0] in {"AC", "----"}:
            continue
        require(re.fullmatch(r"AC-[A-Za-z0-9_-]+", cells[0]) is not None,
                "Invalid AC test mapping")
        require(bool(cells[1]) and len(cells[1]) <= 500,
                "Test mapping needs a bounded test identifier")
        require(bool(cells[2]) and len(cells[2]) <= 100,
                "Test mapping needs a bounded level")
        result.append({"ac": cells[0], "test": cells[1], "level": cells[2]})
    require(bool(result), "Proposal needs test mappings")
    return result
