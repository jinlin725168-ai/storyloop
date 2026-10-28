"""External autonomous loop for approved product changes.

Resume the run, prepare the contract and prove RED, pass design and characterization, then loop
implement → boundary audit → layered verification → review until pass or pause. The gates live in
project_loop_gates, the agent-call guards in project_loop_guards, the completion stage in
project_loop_finish and the acceptance-repair channel in acceptance_repair.
"""

from __future__ import annotations

from .errata import defer_errata, release_errata

from datetime import datetime, timezone
from pathlib import Path

from . import cost_resume
from .run_services import LoopServices
from .run_transitions import transition, RUNNABLE
from .run_guards import layers_green
from .default_loop_services import _default_services
from .cross_vendor import prepare_run
from .cost_limits import check_before_call
from .answers import _pending as _unanswered, load_answers
from .baseline import _git, load_baseline, load_project_config, verify_baseline
from .base_follow import merge_blocking_base
from .boundaries import capture_snapshot  # noqa: F401  # 兼容再导出：阶段 D 拆分后保留旧入口
from .changes import load_change
from .change_types import type_of
from .intake import require_intake
from .routing_state import is_routed, open_routing, run_config
from .manual_acceptance import load_manual_acceptance, pending_manual_criteria  # noqa: F401  # 兼容再导出：阶段 D 拆分后保留旧入口
from .failure_attribution import layer_attribution, next_mode
from .facts import merge_facts
from .models import StoryLoopError, require, text
from .project_boundaries import audit_project_changes
from .project_boundaries import _changed_paths
from .project_contracts import validate_agent_summary, validate_review  # noqa: F401  # 兼容再导出：阶段 D 拆分后保留旧入口
from .project_git import current_branch, start_feature
from .project_loop_finish import (complete_change, finalize_change, finish_merged, finish_receipting,
                                  finish_verified, passed_result)
from .planning import review_focus  # noqa: F401  # 兼容再导出：阶段 D 拆分后保留旧入口
from .routing_execution import prepare_execution, candidate_l3
from .routing_risks import merge_routing_risks
from .routing_upgrade import enforce_forbidden_paths
from .project_loop_gates import characterize_paths, design_plan, uncharacterized_paths
from .guarded_calls import read_only_call, write_safely  # noqa: F401  # 兼容再导出：阶段 D 拆分后保留旧入口
from .project_loop_guards import (require_control_unchanged, require_git_visible,  # noqa: F401  # 兼容再导出：阶段 D 拆分后保留旧入口
                                  verify_read_only_snapshot)
from .project_loop_failures import (apply_overrule, epoch_of, record_boundary, record_failure,
                                    require_routed_choice, route_review)
from .project_loop_support import control_digest, run_path, save_run  # noqa: F401  # 兼容再导出：阶段 D 拆分后保留旧入口
from .run_stops import pause
from .oracle_counts import check_layers
from .project_preparation import _prepare_acceptance
from .project_profile import layer_commands
from .processutil import redact, redaction_prefix
from .system_smoke import run_system_smoke_isolated  # noqa: F401  # 兼容再导出：阶段 D 拆分后保留旧入口
from .smoke import RESULT_EVIDENCE  # noqa: F401  # 兼容再导出：阶段 D 拆分后保留旧入口
from .smoke import suite_relative  # noqa: F401  # 兼容再导出：阶段 D 拆分后保留旧入口
from .skills import role_skill_versions
from .smoke_runner import implementation_digest, run_commands_isolated, run_smoke_isolated  # noqa: F401  # 兼容再导出：阶段 D 拆分后保留旧入口
from .storage import load_json, locked


def run_change(project: Path, change_id: str, services: LoopServices | None = None,
               overrule: str | None = None, implementer: str | None = None) -> dict:
    """Run or resume an approved change until pass or an explicit pause state."""
    if overrule is not None:
        overrule = text(overrule, "overrule reason")
    project = Path(project).resolve()
    with locked(project):
        try:
            return _run_change_locked(project, change_id, services, overrule, implementer)
        except StoryLoopError as exc:
            if exc.code == "WORKSPACE_TAINTED":
                path = run_path(project, change_id)
                if path.exists():
                    state = load_json(path)
                    state["interrupted_status"] = state["status"]
                    pause(project, state, "WORKSPACE_TAINTED", error=redact(str(exc)))
            raise


def _run_change_locked(
    project: Path, change_id: str, services: LoopServices | None, overrule: str | None = None,
    implementer: str | None = None,
) -> dict:
    project = Path(project).resolve()
    change = load_change(project, change_id)
    if type_of(change["product"]) == "base":
        require(overrule is None, "Base changes do not use review overrules")
        from .base_execution import run_base_change
        return run_base_change(project, change, implementer, _new_run_state, services)
    require(implementer is None, "--implementer is only for base changes")
    saved_path = run_path(project, change_id)
    saved = load_json(saved_path) if saved_path.exists() else None
    if overrule:
        require(saved and saved.get("routed") and saved.get("status") in {"ORACLE_DEFECT", "NEEDS_SPEC_FIX"},
                "--overrule 只能用于审查判定的 ORACLE_DEFECT 或 NEEDS_SPEC_FIX")
    if saved and saved.get("status") == "PASSED":
        require(change["status"] == "PASSED", "Completed run has inconsistent change state")
        return passed_result(saved)
    finalizing = saved and saved.get("status") == "FINALIZING"
    require((change["status"] == "APPROVED" or
             (change["status"] == "PASSED" and finalizing)) and change["approval"],
            "Change must be approved before automatic execution")
    services = services or _default_services()
    config = load_project_config(project)
    prepare_run(project, config)
    routing = None
    if saved is None and is_routed(change):
        require_intake(project, change)
        routing = open_routing(change, config.get("routing"), config)
    state, start_attempt, fresh = _open_run(project, change, routing)
    config = run_config(config, change, state)
    save_run(project, state)
    cost_resume.restore(project, state)
    try:
        paused = _resume_and_prepare(project, change, state, services, config, fresh, overrule)
    except StoryLoopError as exc:
        if exc.code == "COST_LIMIT_REACHED":
            return cost_resume.stop(project, state, exc)
        raise
    if paused is not None:
        return paused
    return _attempts(project, change, state, services, config, start_attempt)


def _resume_and_prepare(project: Path, change: dict, state: dict, services: LoopServices,
                        config: dict, fresh: bool, overrule: str | None) -> dict | None:
    change_id = change['change_id']
    state.pop("stop", None)
    require_routed_choice(state, overrule)
    if overrule:
        routed = apply_overrule(project, state, overrule)
        if routed is not None:
            return routed
    if state["status"] == "NEEDS_INPUT":
        require(not _unanswered(state, load_answers(project, change_id)),
                "Answer the pending questions first: storyloop answer <project> --change "
                f"{change_id} --answer ..., or run interactively in a terminal")
    if state["status"] == "VERIFIED":
        return finish_verified(project, change, state)
    if state["status"] == "MANUAL_ACCEPTANCE_REQUIRED":
        return _resume_manual_acceptance(project, change, state)
    if state["status"] == "MERGED":
        return finish_merged(project, change, state)
    if state["status"] == "RECEIPTING":
        return finish_receipting(project, change, state)
    if state["status"] == "FINALIZING":
        return finalize_change(project, change, state)
    if state["status"] == "BASE_CHANGE_REQUIRED" and state.get("blocked_by"):
        return pause(project, state, "BASE_CHANGE_REQUIRED", blocked_by=state["blocked_by"],
                     base_proposal=state.get("base_proposal"),
                     violations=state.get("violations", []))
    if fresh or state["status"] in {"PREPARING", "CONTRACT_READY", "CONTRACT_COMMITTED", "EXISTING_GREEN", "ORACLE_DEFECT"}:
        paused = _prepare_acceptance(project, change, services, state)
        if paused is not None:
            return paused
    return prepare_execution(project, change, state, services, config)


def _attempts(project: Path, change: dict, state: dict, services: LoopServices,
              config: dict, start_attempt: int) -> dict:
    from .run_attempts import attempts
    return attempts(project, change, state, services, config, start_attempt,
                    _run_attempt, _hand_back_attempt)


def load_run_state(project: Path, change_id: str) -> dict:
    return load_json(run_path(Path(project).resolve(), change_id))


def _open_run(project: Path, change: dict, routing: dict | None = None) -> tuple[dict, int, bool]:
    change_id = change["change_id"]
    path = run_path(project, change_id)
    if path.exists():
        state = load_json(path)
        resumable = RUNNABLE
        require(state.get("change_id") == change_id and state.get("status") in resumable,
                "Run state cannot be resumed")
        require(state.get("contract_digest") == change["approval"]["contract_digest"],
                "Saved run belongs to a different approved product contract")
        current = current_branch(project)
        if state["status"] == "VERIFIED" and current == state.get("source_branch"):
            transition(project, state, "MERGED", event="recover_merge")
            save_run(project, state)
        expected = (state.get("source_branch")
                    if state["status"] in {"MERGED", "RECEIPTING", "FINALIZING"}
                    else state.get("feature_branch"))
        require(current == expected, "Resume the saved StoryLoop branch")
        if state["status"] != "RECEIPTING":
            if state.get("blocked_by"):
                _follow_base_upgrade(project, state)
                verify_baseline(project, resume=True)
            else:
                verify_baseline(
                    project, resume=state["status"] not in {"MERGED", "FINALIZING"}
                )
                _follow_base_upgrade(project, state)
        return state, int(state.get("attempt", 0)) + 1, False
    return _start_run(project, change, routing)


def _start_run(project: Path, change: dict, routing: dict | None) -> tuple[dict, int, bool]:
    change_id = change['change_id']
    link = change.get("supersedes")
    if link:
        branch = current_branch(project)
        require(branch == link["source_branch"],
                f"{change_id} 修订自 {link['change_id']}：新运行要从源分支开始，"
                f"git checkout {link['source_branch']}（当前在 {branch}）")
    verify_baseline(project)
    state = _new_run_state(project, change_id, start_feature(project, change_id),
                           change["approval"]["contract_digest"])
    if routing is not None:
        state["routing"] = routing
    state["skills_used"] = role_skill_versions(project, load_project_config(project).get("profile", {}).get("stacks", []))
    save_run(project, state)
    return state, 1, True


def _follow_base_upgrade(project: Path, state: dict) -> None:
    """A base-update made while this feature was paused moves the source branch on purpose; the
    run adopts the upgraded head (verify_baseline already proved it is an ancestor). Any other
    movement of the source branch is still refused at completion."""
    baseline = load_baseline(project)
    if baseline["source_head"] == state["source_head"] or state["status"] in {"MERGED", "FINALIZING"}:
        return
    if state.get("blocked_by"):
        merge_blocking_base(project, state, baseline)
        return
    # A run may start after legal prose commits; its older BASE is not a new upgrade.
    if _git(project, "merge-base", baseline["source_head"], state["source_head"]).strip() \
            == baseline["source_head"]:
        return
    state.setdefault("base_upgrades", []).append({
        "baseline_version": baseline["baseline_version"], "from": state["source_head"],
        "to": baseline["source_head"]})
    state["source_head"] = baseline["source_head"]
    save_run(project, state)


@defer_errata(manual=True)
def _run_attempt(
    project: Path, change: dict, state: dict,
    services: LoopServices, config: dict,
) -> dict | None:
    change_id = change["change_id"]
    state.pop("questions", None)  # answered by reaching a new attempt; never carry stale ones
    answers = load_answers(project, change_id)
    state["answers"] = answers
    failures = state["failures"]
    adjacent = failures and failures[-1].get("attempt") == state["attempt"] - 1
    state["mode"] = next_mode(failures if adjacent else [], epoch_of(state))
    context = {"change": change, "attempt": state["attempt"],
               "max_attempts": config["max_attempts"],
               "failures": list(state["failures"]), "answers": answers,
               "facts": list(state.get("facts") or []),
               "characterization": state.get("characterization"),
               "mode": state["mode"],
               "design_plan": design_plan(state),
               **({"routing_tier": state["routing"]["effective_tier"]} if is_routed(change, state) else {})}
    preexisting = set(_changed_paths(project))
    saved = cost_resume.candidate(project, state)
    response, touched = saved or write_safely(project, change_id, lambda: _implement_answer(services, project, context))
    if "_contract_error" in response:
        # The work on disk stays; the malformed answer costs this attempt and is fed back as a failure.
        return record_failure(project, state, {"attempt": state["attempt"], "contract": {
            "status": "INVALID", "message": response["_contract_error"]}}, "implement")
    cost_resume.checkpoint(project, state, response, touched)
    return _judge_attempt(project, change, state, services, config, response, touched, preexisting)


def _judge_attempt(project: Path, change: dict, state: dict, services: LoopServices, config: dict,
                   response: dict, touched: list, preexisting: set[str]) -> dict | None:
    """Apply deterministic gates to one valid implement response."""
    change_id = change["change_id"]
    validate_agent_summary(response)
    merge_facts(project, state, response.get("facts"), "implement")
    if response.get("design"):
        state["design"] = response["design"]
    boundary = audit_project_changes(project, change_id)
    require_git_visible(touched, boundary)
    enforce_forbidden_paths(state, boundary)
    if boundary["violations"]:
        return record_boundary(project, state, boundary, touched, preexisting)
    release_errata()
    if response["questions"]:
        return pause(project, state, "NEEDS_INPUT", questions=response["questions"])
    risk_pause = _risk_updates(project, state, response, 'implement')
    if risk_pause is not None:
        return risk_pause
    replanned, result = candidate_l3(project, change, state, services, config, boundary)
    if replanned:
        return result
    uncharacterized = uncharacterized_paths(project, state, boundary, config)
    if uncharacterized:
        require(services.characterize is not None and config['profile'].get('test_roots')
                and layer_commands(config['profile'], 'unit'),
                'Changed existing paths require a configured characterization gate')
        # Stop before restoring candidate bytes when the next characterization cannot start.
        check_before_call(project, change_id, config)
        # Existing behaviour must be locked before it is changed: restore, lock, and try again.
        _git(project, "checkout", state["contract_commit"], "--", *uncharacterized)
        paused = characterize_paths(project, change, state, services, config, extend=uncharacterized)
        if paused is not None:
            return paused
        return record_failure(project, state, {"attempt": state["attempt"], "characterization": {
            "status": "FAILED", "uncharacterized": uncharacterized}}, "implement")
    return _verify_attempt(project, change, state, services, config, boundary)


def _verify_attempt(project, change, state, services, config, boundary):
    layers, boundary = _run_checks_safely(project, change, config, boundary)
    layers = check_layers(layers, state.get("oracle"))
    if boundary["violations"]:
        return record_boundary(project, state, boundary)
    if not layers_green(layers) and layer_attribution(layers) == "environment":
        return record_failure(project, state, {"attempt": state["attempt"], **layers},
                              "environment")
    review = _perform_review(project, change, state, services, boundary, layers)
    verdict = review["verdict"]
    merge_facts(project, state, review.get("facts"), "review")
    risk_pause = _risk_updates(project, state, review, 'review')
    if risk_pause is not None:
        return risk_pause
    replanned, result = candidate_l3(project, change, state, services, config, boundary)
    if replanned:
        return result
    if verdict == "needs_input" and layers_green(layers):
        return pause(project, state, "NEEDS_INPUT", questions=review["questions"])
    if verdict == "fail" or not layers_green(layers):
        return route_review(project, change, state, review, layers)
    verified = {"boundary": boundary, "layers": layers, "review": review}
    paused = _manual_acceptance_gate(project, change, state, verified)
    if paused is not None:
        return paused
    return complete_change(project, change, state, **verified)


def _hand_back_attempt(project: Path, state: dict, error: StoryLoopError) -> None:
    """An agent that never answered (crash, timeout, rate limit) did not spend the quality budget.

    `max_attempts` counts answers the loop could judge (decision 0005), so the interrupted attempt is
    handed back: the next `run` retries the same attempt number. The failure stays on record for the
    log and the outer loop. A writer that changed the workspace before dying is WORKSPACE_TAINTED
    instead, and `reset` hands that attempt back.
    """
    moment = datetime.now(timezone.utc).isoformat(timespec="seconds")
    state.setdefault("agent_failures", []).append({
        "attempt": state["attempt"], "role": error.details.get("role"), "code": error.code,
        "message": redact(redaction_prefix(str(error), 600))[:600], "at": moment})
    consumed = state.get('planning', {}).get('consumed_attempt', 0)
    state["attempt"] = max(int(state["attempt"]) - 1, consumed)
    from .stop_report import build_stop
    state["stop"] = build_stop(project, state, error.code, {"error": redact(str(error))})
    save_run(project, state)


def _implement_answer(services: LoopServices, project: Path, context: dict) -> dict:
    """The implement call; an answer that breaks the role schema even after the adapter's own
    repairs becomes a marker instead of an exception, so it is judged like any failed attempt."""
    try:
        return services.implement(project, context)
    except StoryLoopError as exc:
        if exc.code == "AGENT_UNAVAILABLE":
            exc.details.setdefault("role", "implement")
        if exc.code != "AGENT_CONTRACT_INVALID":
            raise
        return {"_contract_error": str(exc)}


def _manual_acceptance_gate(project: Path, change: dict, state: dict, verified: dict) -> dict | None:
    from .run_attempts import manual_acceptance_gate
    return manual_acceptance_gate(project, change, state, verified)


def _resume_manual_acceptance(project: Path, change: dict, state: dict) -> dict:
    from .run_attempts import resume_manual_acceptance
    return resume_manual_acceptance(project, change, state)


def _run_checks_safely(project: Path, change: dict, config: dict,
                       prior_boundary: dict) -> tuple[dict, dict]:
    from .run_attempts import run_checks_safely
    return run_checks_safely(project, change, config, prior_boundary)


def _perform_review(project: Path, change: dict, state: dict, services: LoopServices,
                    boundary: dict, layers: dict) -> dict:
    from .run_attempts import perform_review
    return perform_review(project, change, state, services, boundary, layers)


def _new_run_state(project: Path, change_id: str, started: dict, contract_digest: str) -> dict:
    state = {"version": 1, "change_id": change_id, "attempt": 0,
            "feature_branch": started["branch"], "source_branch": started["source_branch"],
            "source_head": started["source_head"], "contract_digest": contract_digest,
            "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "failures": []}
    transition(project, state, "PREPARING", event="start")
    return state


def _risk_updates(project, state, response, role):
    try:
        merge_routing_risks(project, state, response.get('routing_risks'), role)
    except StoryLoopError as exc:
        return pause(project, state, 'NEEDS_INPUT', questions=[str(exc)])
    return None
