"""Bounded persistence for versioned run records; no transition decisions."""
from pathlib import Path
import hashlib
import json
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
    "HUMAN_IMPLEMENTATION_REQUIRED",
    "BASE_REVIEW_REQUIRED",
})
DISPLAY_STOPS = ROUTING_STATUSES | ORDINARY_STOPS
RUN_STOPS = ORDINARY_STOPS | {"DESIGN_APPROVAL_REQUIRED", "DESIGN_REVISION_REQUIRED", "SPLIT_RECOMMENDED"}
ACTIVE_STATUSES = frozenset({"PREPARING", "CONTRACT_READY", "CONTRACT_COMMITTED", "IMPLEMENTING",
                             "VERIFIED", "MERGED", "RECEIPTING", "FINALIZING"})


def save_run(project: Path, state: dict) -> None:
    write_json(run_path(project, state["change_id"]), state, max_bytes=8_000_000)


def run_path(project: Path, change_id: str) -> Path:
    return project / ".storyloop" / "state" / "runs" / f"{change_id}.json"


def base_design_approved(project: Path | str, state: dict) -> bool:
    """Check saved plan and approval bytes before presenting the next run command."""
    plan, approval = state.get("base_design"), state.get("base_design_approval")
    if not isinstance(plan, dict) or not isinstance(approval, dict):
        return False
    if not plan.get("plan_id") or approval.get("plan_id") != plan["plan_id"]:
        return False
    change_id = state.get("change_id")
    if not isinstance(change_id, str):
        return False
    for kind, value, folder in (("base_design", plan, "base-designs"),
                                ("base_design_approval", approval, "base-design-approvals")):
        path = Path(project) / ".storyloop/state" / folder / f"{change_id}.json"
        try:
            raw = path.read_bytes()
            if (hashlib.sha256(raw).hexdigest() != state.get(kind + "_sha256")
                    or json.loads(raw) != value):
                return False
        except (OSError, ValueError, UnicodeError):
            return False
    return True
