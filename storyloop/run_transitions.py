"""Explicit lifecycle edges and the sole assignment of aggregate/run status."""
from pathlib import Path
from typing import Callable
from .models import require
from .run_guards import guard_updates, guard_g1, guard_g2, guard_g3, reset_status, guard_cost_restore, recover_completed_merge
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
    "MANUAL_ACCEPTANCE_REQUIRED": {"IMPLEMENTING", "BASE_REVIEW_REQUIRED",
                                   "MANUAL_ACCEPTANCE_REQUIRED"},
    "HUMAN_IMPLEMENTATION_REQUIRED": {"IMPLEMENTING", "HUMAN_IMPLEMENTATION_REQUIRED"},
    "BASE_REVIEW_REQUIRED": {"IMPLEMENTING", "BASE_REVIEW_REQUIRED"},
    "NO_PROGRESS": {"IMPLEMENTING", "NO_PROGRESS", "BASE_CHANGE_REQUIRED", "BOUNDARY_VIOLATION"},
    # The existing planning API may pause on its committed contract, before RED.
    "NEEDS_INPUT": PLANNING_SOURCES | {"CONTRACT_COMMITTED"},
    **{target: PLANNING_SOURCES for target in {
        "NEEDS_SPEC_FIX", "BOUNDARY_VIOLATION", "BASE_CHANGE_REQUIRED",
        "CHARACTERIZATION_FAILED", "BUDGET_EXHAUSTED", "DESIGN_APPROVAL_REQUIRED",
        "DESIGN_REVISION_REQUIRED", "SPLIT_RECOMMENDED"}},
}
# Supersede's existing approval/identity gate accepted every load_change read status.
# RUNNING/PAUSED/FAILED stay legacy inputs only; no event produces those values.
_CHANGE_READ_STATUSES = frozenset({"DRAFT", "APPROVED", "RUNNING", "PASSED", "PAUSED", "FAILED", "SUPERSEDED"})
# Guarded implementation/completion events are added with their checks, never as empty stubs.
TRANSITIONS = {
    "repair_red": frozenset((source, "IMPLEMENTING") for source in REPAIRABLE),
    "overrule": frozenset((source, "IMPLEMENTING") for source in {"ORACLE_DEFECT", "NEEDS_SPEC_FIX"}),
    "consistency_overrule": frozenset((source, "IMPLEMENTING") for source in {"ORACLE_DEFECT", "NEEDS_SPEC_FIX"}),
    "design_reapprove": frozenset({("CHARACTERIZATION_FAILED", "DESIGN_APPROVAL_REQUIRED")}),
    "design_replan": frozenset((source, "DESIGN_REVISION_REQUIRED") for source in {
        "DESIGN_APPROVAL_REQUIRED", "DESIGN_REVISION_REQUIRED", "SPLIT_RECOMMENDED", "NEEDS_INPUT"}),
    "l3_replan": frozenset({("IMPLEMENTING", "DESIGN_REVISION_REQUIRED")}),
    "supersede": frozenset((source, "SUPERSEDED") for source in SUPERSEDABLE | {"SUPERSEDED"}),
    "supersede_result": frozenset((source, "SUPERSEDED") for source in SUPERSEDABLE | {"SUPERSEDED"}),
    "recover_merge": frozenset({("VERIFIED", "MERGED")}),
    "reset": frozenset({("MANUAL_ACCEPTANCE_REQUIRED", "IMPLEMENTING")} | {
        ("WORKSPACE_TAINTED", target) for target in {"PREPARING", "CONTRACT_COMMITTED", "IMPLEMENTING", "DESIGN_REVISION_REQUIRED"}}),
    "cost_restore": frozenset(("COST_LIMIT_REACHED", target) for target in PLANNING_SOURCES | {
        "PREPARING", "CONTRACT_READY", "CONTRACT_COMMITTED"}),
    "verified": frozenset((source, "VERIFIED") for source in {"IMPLEMENTING", "MANUAL_ACCEPTANCE_REQUIRED"}),
    "merge": frozenset({("VERIFIED", "MERGED")}),
    "receipting": frozenset({("MERGED", "RECEIPTING")}),
    "finalizing": frozenset({("RECEIPTING", "FINALIZING")}),
    "passed": frozenset({("FINALIZING", "PASSED")}),
    "start": frozenset({(None, "PREPARING")}),
    "red_ready": frozenset((source, "IMPLEMENTING") for source in {
        "CONTRACT_COMMITTED", "EXISTING_GREEN", "ORACLE_DEFECT"}),
    "base_green_ready": frozenset({("CONTRACT_COMMITTED", "IMPLEMENTING")}),
    "human_candidate_ready": frozenset({("HUMAN_IMPLEMENTATION_REQUIRED", "IMPLEMENTING")}),
    # Pre-merge interruptions discard the entire attempt; only human commits survive.
    "base_restart": frozenset((source, target) for source in {
        "CONTRACT_COMMITTED", "IMPLEMENTING", "HUMAN_IMPLEMENTATION_REQUIRED",
        "BASE_REVIEW_REQUIRED", "MANUAL_ACCEPTANCE_REQUIRED",
        "VERIFIED", "WORKSPACE_TAINTED",
    } for target in {"CONTRACT_COMMITTED", "HUMAN_IMPLEMENTATION_REQUIRED"}),
    "base_baseline_recheck": frozenset({("WORKSPACE_TAINTED", "FINALIZING")}),
    "begin_attempt": frozenset((source, "IMPLEMENTING") for source in PLANNING_SOURCES),
    "characterization_resumed": frozenset({("CHARACTERIZATION_FAILED", "IMPLEMENTING")}),
    "contract_ready": frozenset({("PREPARING", "CONTRACT_READY")}),
    "contract_committed": frozenset({("CONTRACT_READY", "CONTRACT_COMMITTED")}),
    "stop": frozenset((source, target) for target, sources in STOP_SOURCES.items() for source in sources),
    "change_passed": frozenset({("APPROVED", "PASSED"), ("PASSED", "PASSED")}),
    "change_superseded": frozenset((source, "SUPERSEDED") for source in _CHANGE_READ_STATUSES),
}


def transition(project: Path, state: dict, target: str, *, event: str,
               change: dict | None = None, context: dict | None = None,
               updates: dict | None = None, effect: Callable[[], dict] | None = None) -> dict:
    """Validate an explicit edge, check its fixed guard, then apply a successful effect."""
    source = state.get("status")
    # A historical snapshot is compared directly, before finite-table hashing; old reset
    # records accepted nonstandard JSON values and this refactor must not tighten them.
    if event == "reset":
        legal = source in RESETTABLE and target == reset_status(state)
    elif event == "cost_restore":
        legal = (source == "COST_LIMIT_REACHED" and "cost_resume_status" in state
                 and target == state["cost_resume_status"])
    else:
        legal = (source, target) in TRANSITIONS.get(event, ())
    require(legal,
            f"Illegal run transition: {event} {source} -> {target}")
    guard_updates(updates if updates is not None else {})
    if event in {"red_ready", "base_green_ready", "begin_attempt", "characterization_resumed", "repair_red"}:
        guard_g1(project, state, context or {})
    if event == "verified":
        require(isinstance(change, dict), "Verification transition needs its approved change")
        guard_g2(project, change, state, context or {})
    if event == "merge":
        guard_g3(project, state, context or {})
    recovered = recover_completed_merge(project, state) if event == "recover_merge" else None
    if event == "cost_restore":
        guard_cost_restore(project, state)
    result = effect() if effect is not None else {}
    guard_updates(result)
    if recovered is not None:
        require(all(value.get("merge_commit", recovered) == recovered for value in (result, updates or {})),
                "Recovered merge identity cannot be replaced")
        state.update(merge_commit=recovered)
    state.update(result)
    state.update(updates if updates is not None else {})
    state["status"] = target
    return result


def set_change_status(project: Path, change: dict, status: str) -> dict:
    """Only completion/supersession own aggregate lifecycle updates; preserve the caller."""
    require(status in {"PASSED", "SUPERSEDED"}, "Unsupported aggregate lifecycle status")
    updated = dict(change)
    event = "change_passed" if status == "PASSED" else "change_superseded"
    transition(project, updated, status, event=event)
    write_json(project / ".storyloop/state/changes" / f"{change['change_id']}.json",
               updated, max_bytes=2_000_000)
    return updated
