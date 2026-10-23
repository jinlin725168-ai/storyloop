"""Small command adapter for running CI and optionally recording its result."""

from __future__ import annotations

from pathlib import Path

from .ci_results import record_ci_result
from .changes import load_change
from .smoke_runner import run_smoke_isolated
from .system_smoke import run_system_smoke_isolated


def run_ci(project: Path, change_id: str) -> dict:
    """Run acceptance before system smoke and combine their isolated results."""
    acceptance = run_smoke_isolated(project, load_change(project, change_id))
    smoke = run_system_smoke_isolated(project)
    status = "PASSED" if acceptance["status"] == "PASSED" and smoke["status"] in {"PASSED", "SKIPPED"} else "FAILED"
    return {"status": status, "change_id": change_id, "acceptance": acceptance, "smoke": smoke}


def run_ci_command(project: Path, change_id: str, record: Path | None) -> dict:
    result = run_ci(project, change_id)
    if record is not None:
        record_ci_result(record, project, result)
    return result
