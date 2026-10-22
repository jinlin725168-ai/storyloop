"""Bounded persistence for versioned run records; no transition decisions."""
from pathlib import Path
from .storage import write_json

# These values describe the on-disk/report format; transition policy lives in the kernel.
ROUTING_STATUSES = frozenset({
    "PROJECT_NOT_READY", "READINESS_APPROVAL_REQUIRED", "EXPLORATION_RECOMMENDED",
    "ORACLE_INFEASIBLE", "DESIGN_APPROVAL_REQUIRED", "DESIGN_REVISION_REQUIRED", "SPLIT_RECOMMENDED",
})
ORDINARY_STOPS = frozenset({
    "NEEDS_INPUT", "ORACLE_DEFECT", "EXISTING_GREEN", "CHARACTERIZATION_FAILED",
    "BOUNDARY_VIOLATION", "BASE_CHANGE_REQUIRED", "WORKSPACE_TAINTED", "COST_LIMIT_REACHED",
    "BUDGET_EXHAUSTED", "NO_PROGRESS", "MANUAL_ACCEPTANCE_REQUIRED", "NEEDS_SPEC_FIX",
})
DISPLAY_STOPS = ROUTING_STATUSES | ORDINARY_STOPS
RUN_STOPS = ORDINARY_STOPS | {"DESIGN_APPROVAL_REQUIRED", "DESIGN_REVISION_REQUIRED", "SPLIT_RECOMMENDED"}
ACTIVE_STATUSES = frozenset({"PREPARING", "CONTRACT_READY", "CONTRACT_COMMITTED", "IMPLEMENTING",
                             "VERIFIED", "MERGED", "RECEIPTING", "FINALIZING"})


def save_run(project: Path, state: dict) -> None:
    write_json(run_path(project, state["change_id"]), state, max_bytes=8_000_000)


def run_path(project: Path, change_id: str) -> Path:
    return project / ".storyloop" / "state" / "runs" / f"{change_id}.json"
