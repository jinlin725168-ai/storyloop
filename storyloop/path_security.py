"""Cross-platform repository path validation without following links."""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
import stat

from .models import require


def repository_relative(value: object, label: str = "path") -> str:
    """Normalize a relative path and reject Windows and POSIX escape syntax."""
    require(isinstance(value, str) and bool(value.strip()), f"{label} is required")
    normalized = value.replace("\\", "/")
    drive = len(normalized) >= 2 and normalized[0].isalpha() and normalized[1] == ":"
    require(not normalized.startswith("/") and not drive and ":" not in normalized,
            f"{label} must be repository-relative")
    normalized = normalized.rstrip("/")
    path = PurePosixPath(normalized)
    require(normalized not in {"", "."} and ".." not in path.parts,
            f"Unsafe {label}")
    return path.as_posix()


def safe_target(
    root: Path, relative: object, label: str = "path",
    target_directory: bool = False,
) -> Path:
    """Resolve a target beneath root and reject linked/reparse ancestors."""
    root = Path(root).resolve()
    normalized = repository_relative(relative, label)
    target = root.joinpath(*PurePosixPath(normalized).parts)
    resolved = target.resolve(strict=False)
    require(os.path.commonpath([str(root), str(resolved)]) == str(root),
            f"Unsafe {label}")
    _reject_unsafe_chain(root, target, target_directory)
    return target


def safe_absolute_target(path: Path, label: str = "path") -> Path:
    """Reject linked/reparse/hard-linked components on an absolute write path."""
    path = Path(path).absolute()
    require(bool(path.anchor), f"{label} must be absolute")
    _reject_unsafe_chain(Path(path.anchor), path, False)
    return path


def _reject_unsafe_chain(root: Path, target: Path, target_directory: bool = False) -> None:
    current = root
    _require_safe_entry(current, allow_directory=True)
    for part in target.relative_to(root).parts:
        current = current / part
        if current.exists() or current.is_symlink():
            _require_safe_entry(current, allow_directory=current != target or target_directory)


def _require_safe_entry(path: Path, allow_directory: bool) -> None:
    metadata = os.lstat(path)
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    require(not path.is_symlink() and not getattr(metadata, "st_file_attributes", 0) & reparse,
            "Links and reparse points are not allowed in controlled paths")
    if stat.S_ISREG(metadata.st_mode):
        require(metadata.st_nlink == 1, "Hard links are not allowed in controlled paths")
    elif not allow_directory:
        require(False, "Controlled target must be a regular file")
