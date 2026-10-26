"""Bounded, no-follow inventory for attributing one agent call's workspace files."""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

from .models import require


def inventory(project: Path) -> dict[str, str]:
    """Include ignored files and links while excluding the engine's control directories."""
    result: dict[str, str] = {}
    for directory, names, files in os.walk(project, followlinks=False):
        base = Path(directory)
        if base == project:
            names[:] = [name for name in names if name not in {".git", ".storyloop"}]
        for name in list(names):
            path = base / name
            if path.is_symlink() or _junction(path):
                result[path.relative_to(project).as_posix()] = _fingerprint(path)
                names.remove(name)
        for name in files:
            path = base / name
            result[path.relative_to(project).as_posix()] = _fingerprint(path)
            require(len(result) <= 20_000, "Agent workspace inventory exceeds file limit")
    return result


def _junction(path: Path) -> bool:
    method = getattr(path, "is_junction", None)
    return bool(method and method())


def _fingerprint(path: Path) -> str:
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or _junction(path):
        return "link:" + hashlib.sha256(os.readlink(path).encode("utf-8")).hexdigest()
    require(stat.S_ISREG(metadata.st_mode) and metadata.st_size <= 20_000_000,
            "Agent workspace contains an unsupported file")
    return "file:" + hashlib.sha256(path.read_bytes()).hexdigest()


def agent_artifacts(before: dict[str, str], after: dict[str, str]) -> list[str]:
    """A prior untracked/ignored file may not be overwritten by an agent and then restored."""
    return sorted(path for path in before.keys() | after.keys()
                  if before.get(path) != after.get(path))


def unlink_agent_file(project: Path, relative: str) -> None:
    """Unlink only a proven new entry; never resolve a final symlink or hardlink."""
    path = project / relative
    require(path.parent.resolve().is_relative_to(project.resolve()),
            "Agent artifact parent escapes project")
    require(not path.parent.is_symlink(), "Agent artifact parent is a link")
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif _junction(path):
        path.rmdir()
