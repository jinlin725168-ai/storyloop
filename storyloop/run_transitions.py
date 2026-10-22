"""Explicit lifecycle edges and the sole assignment of aggregate/run status."""
from pathlib import Path
from typing import Callable
from .models import require
from .run_guards import guard_updates, guard_g1, guard_g2, guard_g3
from .run_state import ACTIVE_STATUSES, DISPLAY_STOPS, RUN_STOPS
from .storage import write_json

RUNNABLE = (ACTIVE_STATUSES | RUN_STOPS) - {"WORKSPACE_TAINTED"}
REPAIRABLE = frozenset({"CONTRACT_COMMITTED", "IMPLEMENTING", "BUDGET_EXHAUSTED", "NEEDS_INPUT",
                        "EXISTING_GREEN", "ORACLE_DEFECT", "NO_PROGRESS"})
RESETTABLE = frozenset({"WORKSPACE_TAINTED", "MANUAL_ACCEPTANCE_REQUIRED"})
SUPERSEDABLE = frozenset({
    "NEEDS_SPEC_FIX", "BUDGET_EXHAUSTED", "NO_PROGRESS", "ORACLE_DEFECT", "EXISTING_GREEN",
    "NEEDS_INPUT", "IMPLEMENTING", "BOUNDARY_VIOLATION", "CHARACTERIZATION_FAILED",
    "DESIGN_APPROVAL_REQUIRED", "DESIGN_REVISION_REQUIRED", "SPLIT_RECOMMENDED",
})
STATE_METADATA = {
    status: dict(runnable=status in RUNNABLE, resettable=status in RESETTABLE,
                 repairable=status in REPAIRABLE, supersedable=status in SUPERSEDABLE,
                 display_stop=status in DISPLAY_STOPS)
    for status in ACTIVE_STATUSES | DISPLAY_STOPS | {"PASSED", "SUPERSEDED"}
}
# Planning keeps a resumed stop until an attempt actually starts; it is not an all-status wildcard.
PLANNING_SOURCES = RUNNABLE - {"PREPARING", "CONTRACT_READY", "CONTRACT_COMMITTED",
                               "VERIFIED", "MERGED", "RECEIPTING", "FINALIZING"}
STOP_SOURCES = {
    "WORKSPACE_TAINTED": ACTIVE_STATUSES | RUN_STOPS,
    "COST_LIMIT_REACHED": PLANNING_SOURCES | {"PREPARING", "CONTRACT_READY", "CONTRACT_COMMITTED"},
    "EXISTING_GREEN": REPAIRABLE,
    "ORACLE_DEFECT": REPAIRABLE | PLANNING_SOURCES,
    "MANUAL_ACCEPTANCE_REQUIRED": {"IMPLEMENTING", "MANUAL_ACCEPTANCE_REQUIRED"},
    "NO_PROGRESS": {"IMPLEMENTING", "NO_PROGRESS"},
    **{target: PLANNING_SOURCES for target in {
        "NEEDS_INPUT", "NEEDS_SPEC_FIX", "BOUNDARY_VIOLATION", "BASE_CHANGE_REQUIRED",
        "CHARACTERIZATION_FAILED", "BUDGET_EXHAUSTED", "DESIGN_APPROVAL_REQUIRED",
        "DESIGN_REVISION_REQUIRED", "SPLIT_RECOMMENDED"}},
}
# Guarded implementation/completion events are added with their checks, never as empty stubs.
TRANSITIONS = {
    "verified": frozenset((source, "VERIFIED") for source in {"IMPLEMENTING", "MANUAL_ACCEPTANCE_REQUIRED"}),
    "merge": frozenset({("VERIFIED", "MERGED")}),
    "receipting": frozenset({("MERGED", "RECEIPTING")}),
    "finalizing": frozenset({("RECEIPTING", "FINALIZING")}),
    "passed": frozenset({("FINALIZING", "PASSED")}),
    "start": frozenset({(None, "PREPARING")}),
    "red_ready": frozenset((source, "IMPLEMENTING") for source in {
        "CONTRACT_COMMITTED", "EXISTING_GREEN", "ORACLE_DEFECT"}),
    "begin_attempt": frozenset((source, "IMPLEMENTING") for source in PLANNING_SOURCES),
    "characterization_resumed": frozenset({("CHARACTERIZATION_FAILED", "IMPLEMENTING")}),
    "contract_ready": frozenset({("PREPARING", "CONTRACT_READY")}),
    "contract_committed": frozenset({("CONTRACT_READY", "CONTRACT_COMMITTED")}),
    "stop": frozenset((source, target) for target, sources in STOP_SOURCES.items() for source in sources),
    "change_passed": frozenset({("APPROVED", "PASSED"), ("PASSED", "PASSED")}),
    "change_superseded": frozenset({("APPROVED", "SUPERSEDED"), ("SUPERSEDED", "SUPERSEDED")}),
}


def transition(project: Path, state: dict, target: str, *, event: str,
               change: dict | None = None, context: dict | None = None,
               updates: dict | None = None, effect: Callable[[], dict] | None = None) -> dict:
    """Validate an explicit edge, check its fixed guard, then apply a successful effect."""
    source = state.get("status")
    require((source, target) in TRANSITIONS.get(event, ()),
            f"Illegal run transition: {event} {source} -> {target}")
    guard_updates(updates if updates is not None else {})
    if event in {"red_ready", "begin_attempt", "characterization_resumed"}:
        guard_g1(project, state, context or {})
    if event == "verified":
        require(isinstance(change, dict), "Verification transition needs its approved change")
        guard_g2(project, change, state, context or {})
    if event == "merge":
        guard_g3(project, state, context or {})
    result = effect() if effect is not None else {}
    guard_updates(result)
    state.update(result)
    state.update(updates if updates is not None else {})
    state["status"] = target
    return result


def set_change_status(project: Path, change: dict, status: str) -> None:
    """Only completion/supersession own aggregate lifecycle updates; preserve the caller."""
    require(status in {"PASSED", "SUPERSEDED"}, "Unsupported aggregate lifecycle status")
    updated = dict(change)
    event = "change_passed" if status == "PASSED" else "change_superseded"
    transition(project, updated, status, event=event)
    write_json(project / ".storyloop/state/changes" / f"{change['change_id']}.json",
               updated, max_bytes=2_000_000)
