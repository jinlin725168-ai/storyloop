"""Bounded persistence for versioned run records; no transition decisions."""
from pathlib import Path
from .storage import write_json


def save_run(project: Path, state: dict) -> None:
    write_json(run_path(project, state["change_id"]), state, max_bytes=8_000_000)


def run_path(project: Path, change_id: str) -> Path:
    return project / ".storyloop" / "state" / "runs" / f"{change_id}.json"
