"""Verification, review and manual acceptance steps of a change attempt."""
from pathlib import Path

from . import cost_resume
from .answers import load_answers
from .baseline import load_project_config
from .boundaries import capture_snapshot
from .manual_acceptance import load_manual_acceptance, pending_manual_criteria
from .models import StoryLoopError, require
from .planning import review_focus
from .project_boundaries import audit_project_changes
from .project_loop_finish import complete_change
from .project_loop_guards import require_control_unchanged, verify_read_only_snapshot
from .project_loop_support import control_digest
from .project_profile import layer_commands
from .run_stops import pause
from .run_transitions import transition
from .project_loop_support import save_run
from .smoke import RESULT_EVIDENCE
from .smoke_runner import implementation_digest, run_commands_isolated, run_smoke_isolated
from .system_smoke import run_system_smoke_isolated


def attempts(project: Path, change: dict, state: dict, services, config: dict,
             start_attempt: int, run_attempt, hand_back_attempt) -> dict:
    """Spend the quality budget on judged answers while preserving resumable failures."""
    while state["attempt"] < config["max_attempts"]:
        state["attempt"] += 1
        transition(project, state, "IMPLEMENTING", event="begin_attempt")
        save_run(project, state)
        try:
            outcome = run_attempt(project, change, state, services, config)
        except StoryLoopError as exc:
            if exc.code == "COST_LIMIT_REACHED":
                return cost_resume.stop(project, state, exc, in_attempt=True)
            state.pop("pending_review", None)
            if not exc.details.get("review_semantic"):
                state.pop("cost_candidate", None)
            save_run(project, state)
            if exc.code == "AGENT_UNAVAILABLE" or exc.details.get("review_semantic"):
                hand_back_attempt(project, state, exc)
            raise
        state.pop("cost_candidate", None)
        save_run(project, state)
        if outcome is not None:
            return outcome
    return pause(project, state, "BUDGET_EXHAUSTED")


def manual_acceptance_gate(project: Path, change: dict, state: dict, verified: dict) -> dict | None:
    """Pause before the pass report until every manual criterion is accepted on this exact code."""
    digest = implementation_digest(project)
    records = load_manual_acceptance(project, change["change_id"])
    pending = pending_manual_criteria(change, records, digest)
    if not pending:
        return None
    return pause(project, state, "MANUAL_ACCEPTANCE_REQUIRED", pending=pending,
                 candidate_sha256=digest, verified=verified,
                 next=f"storyloop accept <project> --change {change['change_id']} "
                      "--criterion <ID> --note <what you verified>, then storyloop run")


def resume_manual_acceptance(project: Path, change: dict, state: dict) -> dict:
    """Continue a paused run once the human accepted the manual criteria on unchanged code."""
    require(implementation_digest(project) == state.get("candidate_sha256"),
            "Workspace changed while waiting for manual acceptance; use `storyloop reset` to retry")
    verified = state.get("verified")
    require(isinstance(verified, dict) and set(verified) == {"boundary", "layers", "review"},
            "Paused run lacks its verification evidence")
    paused = manual_acceptance_gate(project, change, state, verified)
    if paused is not None:
        return paused
    return complete_change(project, change, state, **verified)


def run_checks_safely(project: Path, change: dict, config: dict,
                      prior_boundary: dict) -> tuple[dict, dict]:
    """Run every verification layer in order and recheck the protected workspace."""
    before = capture_snapshot(project, include_control=True, include_ignored=True)
    change_id = change["change_id"]
    control = control_digest(project, change_id, excluded_evidence=RESULT_EVIDENCE)
    profile, timeout = config["profile"], config["check_timeout_seconds"]
    try:
        layers = {
            "unit": run_commands_isolated(project, layer_commands(profile, "unit"), timeout),
            "integration": run_commands_isolated(project, layer_commands(profile, "integration"), timeout),
            "acceptance": run_smoke_isolated(project, change),
            "smoke": run_system_smoke_isolated(project),
            "checks": run_commands_isolated(project, layer_commands(profile, "checks"), timeout),
        }
    finally:
        verify_read_only_snapshot(
            before, capture_snapshot(project, include_control=True, include_ignored=True),
            f".storyloop/evidence/{change_id}/{RESULT_EVIDENCE}",
        )
        require_control_unchanged(
            control_digest(project, change_id,
                           excluded_evidence=RESULT_EVIDENCE) == control,
            "Test command modified Git or the protected control plane",
        )
    boundary = audit_project_changes(project, change_id)
    require(prior_boundary["allowed"] == boundary["allowed"] or boundary["violations"],
            "Audited implementation changed during verification")
    return layers, boundary


def perform_review(project: Path, change: dict, state: dict, services,
                   boundary: dict, layers: dict) -> dict:
    context = {"change": change, "attempt": state["attempt"],
               "facts": list(state.get("facts") or []),
               "layers": layers, "boundary": boundary, "design_plan": design_plan(state),
               "answers": load_answers(project, change["change_id"]),
               "design": state.get("design") or "", **review_focus(state)}
    try:
        from .review_disposition import perform_review as review
        return review(project, state, services, context, load_project_config(project))
    except StoryLoopError as exc:
        if exc.code == "AGENT_UNAVAILABLE":
            exc.details.setdefault("role", "review")
        raise


def design_plan(state: dict):
    from .project_loop_gates import design_plan as plan
    return plan(state)
