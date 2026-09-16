"""Strict, dependency-free loading for bounded v2 JSON configuration."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from .json_contract import loads_strict_json
from .models import StoryLoopError, identifier, require
from .safe_path import project_file
from .stage_config import validate_loop_config

MAX_CONFIG_BYTES = 1024 * 1024


def load_loop_config(project: Path, relative_path: str = "loop.yaml") -> dict:
    """Load strict JSON; .yaml is only a compatibility filename, not a parser."""
    require(Path(relative_path).suffix.lower() in {".json", ".yaml", ".yml"},
            "Loop config must use .json, .yaml, or .yml")
    raw = _read_project_file(project, relative_path, MAX_CONFIG_BYTES)
    value = loads_strict_json(raw.decode("utf-8-sig"), "Loop config", MAX_CONFIG_BYTES)
    return validate_loop_config(value)


def load_stage_payloads(project: Path, relative_path: str | None = None,
                        config: dict | None = None) -> dict:
    """Load an optional strict object of Stage payload objects."""
    if relative_path is None:
        return {}
    require(Path(relative_path).suffix.lower() == ".json",
            "Stage payloads must use .json")
    raw = _read_project_file(project, relative_path, MAX_CONFIG_BYTES)
    value = loads_strict_json(raw.decode("utf-8-sig"), "Stage payloads", MAX_CONFIG_BYTES)
    require(isinstance(value, dict) and len(value) <= 100,
            "Stage payloads must be a bounded object")
    for stage, payload in value.items():
        identifier(stage, "Stage payload id")
        require(isinstance(payload, dict), "Each Stage payload must be an object")
    if config is not None:
        known = {stage["id"] for stage in config["stages"]}
        require(set(value) <= known, "Stage payload references an unknown Stage")
    return value


def _read_project_file(project: Path, relative_path: str, maximum: int) -> bytes:
    path = project_file(project, relative_path, 'Config')
    try:
        status = path.lstat()
        reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        require(stat.S_ISREG(status.st_mode) and status.st_nlink == 1
                and not getattr(status, "st_file_attributes", 0) & reparse,
                "Config must be a regular file")
        require(status.st_size <= maximum, "Config exceeds size limit")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as handle:
            current = os.fstat(handle.fileno())
            require((status.st_dev, status.st_ino) == (current.st_dev, current.st_ino),
                    "Input file changed while opening")
            raw = handle.read(maximum + 1)
        require(len(raw) <= maximum, "Config exceeds size limit")
        raw.decode("utf-8-sig")
        return raw
    except StoryLoopError:
        raise
    except (OSError, UnicodeError) as exc:
        raise StoryLoopError("Cannot read bounded config") from exc
