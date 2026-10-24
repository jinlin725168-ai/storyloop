"""Human confirmation of build commands for an old, frozen project config."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .changes import load_change
from .models import require, text
from .path_security import safe_target
from .project_profile import BASE_BUILD_SUPPLEMENT, _commands
from .storage import load_json, locked, write_json


def confirm_legacy_base_build(project: Path, commands: object, confirmed_by: str) -> dict:
    """Write the supplement only while no approved base change depends on it."""
    project = Path(project).resolve()
    with locked(project):
        config = load_json(safe_target(project, ".storyloop/config.json", "project config"))
        require("base_build_commands" not in config["profile"],
                "New projects keep base_build_commands in config.json")
        _require_no_pending_base(project)
        record = {"version": 1, "base_build_commands": _commands(commands),
                  "confirmed_by": text(confirmed_by, "build config confirmer")}
        path = safe_target(project, BASE_BUILD_SUPPLEMENT, "base build supplement")
        write_json(path, record, max_bytes=64_000)
        return {"status": "CONFIRMED", "path": str(path),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _require_no_pending_base(project: Path) -> None:
    root = project / ".storyloop/state/changes"
    for path in root.glob("BASE-*.json"):
        change = load_change(project, path.stem)
        require(not (change.get("approval") and change["status"] not in {"PASSED", "SUPERSEDED"}),
                f"An approved base change is unfinished: {change['change_id']}; "
                "complete it before changing base-build-config")
