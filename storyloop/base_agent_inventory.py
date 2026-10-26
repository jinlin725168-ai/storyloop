"""Git-visible, no-follow inventory for attributing one agent call's workspace files."""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

from .models import require
from .storage import IGNORED


def inventory(project: Path) -> dict[str, str]:
    """Use Git's ignore rules and the project's existing scratch path policy."""
    from .baseline import _git
    from .project_loop_guards import _is_scratch, scratch_paths

    scratch = scratch_paths(project)
    ignored = {name.casefold() for name in IGNORED | {".storyloop"}}
    result: dict[str, str] = {}
    visible = _git(project, "ls-files", "--cached", "--others", "--exclude-standard", "-z")
    for relative in sorted(set(visible.split("\0")) - {""}):
        if any(part.casefold() in ignored for part in relative.split("/")):
            continue
        if _is_scratch(relative, scratch):
            continue
        path = project / relative
        result[relative] = _fingerprint(path) if path.exists() or path.is_symlink() else "missing"
    return result


def _junction(path: Path) -> bool:
    method = getattr(path, "is_junction", None)
    return bool(method and method())


def _fingerprint(path: Path) -> str:
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or _junction(path):
        return "link:" + hashlib.sha256(os.readlink(path).encode("utf-8")).hexdigest()
    if not stat.S_ISREG(metadata.st_mode):
        return f"special:{stat.S_IFMT(metadata.st_mode)}"
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "file:" + digest.hexdigest()


def unlink_agent_file(project: Path, relative: str) -> None:
    """Unlink only a proven new entry; never resolve a final symlink or hardlink."""
    path = project / relative
    require(path.parent.resolve().is_relative_to(project.resolve()),
            "Agent artifact parent escapes project")
    require(not path.parent.is_symlink(), "Agent artifact parent is a link")
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        path.unlink()
    elif _junction(path):
        path.rmdir()
