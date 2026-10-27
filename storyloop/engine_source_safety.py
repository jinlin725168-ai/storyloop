"""No-follow digest of the loaded StoryLoop source before and after agent calls."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .models import StoryLoopError, require


ENGINE_WARNING = ("StoryLoop engine source may have changed during an agent call; "
                  "inspect and reinstall the engine before running again")


def engine_warning(state: dict) -> str | None:
    """Advise on source drift without treating a same-user process as isolated."""
    expected = state.get("agent_engine_digest")
    if not expected:
        return None
    try:
        current = engine_source_digest(Path(state["engine_source"]))
    except (OSError, StoryLoopError, UnicodeError):
        return ENGINE_WARNING
    return ENGINE_WARNING if current != expected else None


def engine_source_digest(source: Path) -> str:
    """Fingerprint loaded engine files without invoking Git or following links."""
    require(source.is_dir() and not source.is_symlink(), "Engine source directory is unsafe")
    digest = hashlib.sha256()
    entries = sorted(path for path in source.rglob("*")
                     if "__pycache__" not in path.parts and path.suffix != ".pyc")
    require(len(entries) <= 10_000, "Engine source has too many files")
    for path in entries:
        relative = path.relative_to(source).as_posix()
        require(not path.is_symlink(), f"Engine source contains a link: {relative}")
        if not path.is_file():
            continue
        require(path.stat().st_size <= 20_000_000, "Engine source file is too large")
        digest.update(relative.encode("utf-8") + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()
