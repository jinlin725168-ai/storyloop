"""Small command adapter for running CI and optionally recording its result."""

from __future__ import annotations

from pathlib import Path

from .ci_results import record_ci_result
from .smoke_runner import run_ci


def run_ci_command(project: Path, change_id: str, record: Path | None) -> dict:
    result = run_ci(project, change_id)
    if record is not None:
        record_ci_result(record, project, result)
    return result
