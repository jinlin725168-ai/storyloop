"""Project-relative path validation that rejects link-like ancestors."""

from __future__ import annotations

import stat
from pathlib import Path, PurePosixPath

from .models import StoryLoopError, require


def project_file(project: Path, relative_path: str, label: str) -> Path:
    """Return a project file path only when every parent is a real directory."""
    require(isinstance(relative_path, str), f"{label} path must be text")
    pure = PurePosixPath(relative_path)
    require(not pure.is_absolute() and "\\" not in relative_path
            and all(part not in {"", ".", ".."} for part in pure.parts),
            f"Unsafe {label.lower()} path")
    root = Path(project).absolute()
    try:
        _require_directory(root, f"{label} project root")
        current = root
        for part in pure.parts[:-1]:
            current = current / part
            _require_directory(current, f"{label} parent")
        return current / pure.parts[-1]
    except StoryLoopError:
        raise
    except OSError as exc:
        raise StoryLoopError(f"Cannot inspect {label.lower()} path") from exc


def _require_directory(path: Path, label: str) -> None:
    status = path.lstat()
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    is_junction = getattr(path, "is_junction", lambda: False)
    require(stat.S_ISDIR(status.st_mode) and not path.is_symlink()
            and not is_junction()
            and not getattr(status, "st_file_attributes", 0) & reparse,
            f"{label} must be a real directory")
