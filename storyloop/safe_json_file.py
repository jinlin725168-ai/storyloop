"""Bounded, identity-checked reads for control-plane JSON objects."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from .json_contract import loads_strict_json
from .models import StoryLoopError, require


def read_bounded_object(path: Path, maximum: int, label: str) -> dict:
    """Read one regular single-link file without following a replacement link."""
    require(type(maximum) is int and maximum > 0, "Invalid JSON size limit")
    try:
        before = path.lstat()
        _validate(before, maximum, label)
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as handle:
            current = os.fstat(handle.fileno())
            _validate(current, maximum, label)
            require((before.st_dev, before.st_ino) == (current.st_dev, current.st_ino),
                    f"{label} changed while opening")
            raw = handle.read(maximum + 1)
        require(len(raw) <= maximum, f"{label} exceeds size limit")
        value = loads_strict_json(raw.decode("utf-8-sig"), label, maximum)
        require(isinstance(value, dict), f"{label} must be a JSON object")
        return value
    except StoryLoopError:
        raise
    except (OSError, UnicodeError) as exc:
        raise StoryLoopError(f"Cannot read {label}") from exc


def _validate(status: os.stat_result, maximum: int, label: str) -> None:
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    require(stat.S_ISREG(status.st_mode) and status.st_nlink == 1
            and not getattr(status, "st_file_attributes", 0) & reparse,
            f"{label} must be a regular file")
    require(status.st_size <= maximum, f"{label} exceeds size limit")
