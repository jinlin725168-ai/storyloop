"""External autonomous loop for approved product changes.

This module owns the state machine: resume from the saved run, prepare the contract and prove
RED, pass the design and characterization gates, then loop implement → boundary audit → layered
verification → review until the change passes or stops at an explicit pause. The gates live in
project_loop_gates, the agent-call guards in project_loop_guards, the completion stage in
project_loop_finish and the acceptance-repair channel in acceptance_repair.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from typing import Callable

from .answers import _pending as _unanswered, load_answers
from .approvals import materialize_approved_change
from .baseline import _git, load_baseline, load_project_config, verify_baseline
from .boundaries import capture_snapshot
from .changes import load_change
from .manual_acceptance import load_manual_acceptance, pending_manual_criteria
from .models import StoryLoopError, require
from .openspec_docs import validate_with_cli
from .project_boundaries import audit_project_changes
from .project_contracts import validate_agent_summary, validate_review
from .project_git import commit_paths, current_branch, start_feature
from .project_loop_finish import (complete_change, finalize_change, finish_merged, finish_receipting,
                                  finish_verified, passed_result)
from .project_loop_gates import (characterize_paths, design_plan, ensure_characterization, ensure_design,
                                 resume_characterization, uncharacterized_paths)
from .project_loop_guards import (read_only_call, require_control_unchanged, require_git_visible,
                                  verify_read_only_snapshot, write_safely)
from .project_loop_support import (control_digest, pause, pause_for_boundary, relative_files, run_path,
                                   save_run)
from .oracle_counts import check_layers, ensure_baseline
from .project_profile import layer_commands
from .project_receipt import recover_completed_merge
from .system_smoke import run_system_smoke_isolated
from .smoke import (RED_EVIDENCE, RESULT_EVIDENCE, install_smoke_suite, load_smoke_manifest, protection_path,
                    without_manifest_echo)
from .smoke import suite_relative, suite_root as acceptance_suite_root
from .skills import ROLE_SKILLS, used_skills
from .smoke_runner import implementation_digest, run_commands_isolated, run_smoke_isolated
from .storage import load_json, locked



@dataclass(frozen=True)
class LoopServices:
    """Agent operations kept injectable so the controller remains authoritative."""

    generate_smoke: Callable[[Path, dict], dict]
    implement: Callable[[Path, dict], dict]
    review: Callable[[Path, dict], dict]
    characterize: Callable[[Path, dict], dict] | None = None
    design: Callable[[Path, dict], dict] | None = None


def run_change(project: Path, change_id: str, services: LoopServices | None = None) -> dict:
    """Run or resume an approved change until pass or an explicit pause state."""
    with locked(Path(project).resolve()):
        return _run_change_locked(Path(project).resolve(), change_id, services)


def _run_change_locked(
    project: Path, change_id: str, services: LoopServices | None,
) -> dict:
    project = Path(project).resolve()
    change = load_change(project, change_id)
    saved_path = run_path(project, change_id)
    saved = load_json(saved_path) if saved_path.exists() else None
    if saved and saved.get("status") == "PASSED":
        require(change["status"] == "PASSED", "Completed run has inconsistent change state")
        return passed_result(saved)
    finalizing = saved and saved.get("status") == "FINALIZING"
    require((change["status"] == "APPROVED" or
             (change["status"] == "PASSED" and finalizing)) and change["approval"],
            "Change must be approved before automatic execution")
    services = services or _default_services()
    config = load_project_config(project)
    state, start_attempt, fresh = _open_run(project, change)
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
    if fresh or state["status"] in {"PREPARING", "CONTRACT_READY", "CONTRACT_COMMITTED", "EXISTING_GREEN", "ORACLE_DEFECT"}:
        paused = _prepare_acceptance(project, change, services, state)
        if paused is not None:
            return paused
    if state["status"] == "CHARACTERIZATION_FAILED":
        paused = resume_characterization(project, change, state, config)
        if paused is not None:
            return paused
    paused = ensure_design(project, change, state, services, config) or \
        ensure_characterization(project, change, state, services, config)
    if paused is not None:
        return paused
    for attempt in range(start_attempt, config["max_attempts"] + 1):
        state["attempt"] = attempt
        state["status"] = "IMPLEMENTING"
        save_run(project, state)
        try:
            outcome = _run_attempt(project, change, state, services, config)
        except StoryLoopError as exc:
            if exc.code == "WORKSPACE_TAINTED":
                state.update({"status": "WORKSPACE_TAINTED", "error": str(exc)})
                save_run(project, state)
            elif exc.code == "AGENT_UNAVAILABLE":
                _hand_back_attempt(project, state, exc)
            raise
        if outcome is not None:
            return outcome
    return pause(project, state, "BUDGET_EXHAUSTED")


def load_run_state(project: Path, change_id: str) -> dict:
    return load_json(run_path(Path(project).resolve(), change_id))


def _open_run(project: Path, change: dict) -> tuple[dict, int, bool]:
    change_id = change["change_id"]
    path = run_path(project, change_id)
    if path.exists():
        state = load_json(path)
        resumable = {"PREPARING", "CONTRACT_READY", "CONTRACT_COMMITTED",
                     "IMPLEMENTING", "BASE_CHANGE_REQUIRED",
                     "BOUNDARY_VIOLATION", "NEEDS_INPUT", "BUDGET_EXHAUSTED",
                     "CHARACTERIZATION_FAILED", "EXISTING_GREEN", "ORACLE_DEFECT",
                     "VERIFIED", "MANUAL_ACCEPTANCE_REQUIRED", "MERGED", "RECEIPTING",
                     "FINALIZING"}
        require(state.get("change_id") == change_id and state.get("status") in resumable,
                "Run state cannot be resumed")
        require(state.get("contract_digest") == change["approval"]["contract_digest"],
                "Saved run belongs to a different approved product contract")
        current = current_branch(project)
        if state["status"] == "VERIFIED" and current == state.get("source_branch"):
            state.update({"status": "MERGED",
                          "merge_commit": recover_completed_merge(project, state)})
            save_run(project, state)
        expected = (state.get("source_branch")
                    if state["status"] in {"MERGED", "RECEIPTING", "FINALIZING"}
                    else state.get("feature_branch"))
        require(current == expected, "Resume the saved StoryLoop branch")
        if state["status"] != "RECEIPTING":
            verify_baseline(
                project, resume=state["status"] not in {"MERGED", "FINALIZING"}
            )
            _follow_base_upgrade(project, state)
        return state, int(state.get("attempt", 0)) + 1, False
    verify_baseline(project)
    state = _new_run_state(change_id, start_feature(project, change_id),
                           change["approval"]["contract_digest"])
    state["skills_used"] = used_skills(project, sorted({n for names in ROLE_SKILLS.values() for n in names}))
    save_run(project, state)
    return state, 1, True


def _follow_base_upgrade(project: Path, state: dict) -> None:
    """A base-update made while this feature was paused moves the source branch on purpose; the
    run adopts the upgraded head (verify_baseline already proved it is an ancestor). Any other
    movement of the source branch is still refused at completion."""
    baseline = load_baseline(project)
    if baseline["source_head"] == state["source_head"] or state["status"] in {"MERGED", "FINALIZING"}:
        return
    state.setdefault("base_upgrades", []).append({
        "baseline_version": baseline["baseline_version"], "from": state["source_head"],
        "to": baseline["source_head"]})
    state["source_head"] = baseline["source_head"]
    save_run(project, state)


def _run_attempt(
    project: Path, change: dict, state: dict,
    services: LoopServices, config: dict,
) -> dict | None:
    change_id = change["change_id"]
    state.pop("questions", None)  # answered by reaching a new attempt; never carry stale ones
    answers = load_answers(project, change_id)
    state["answers"] = answers
    context = {"change": change, "attempt": state["attempt"],
               "max_attempts": config["max_attempts"],
               "failures": list(state["failures"]), "answers": answers,
               "characterization": state.get("characterization"),
               "design_plan": design_plan(state)}
    response, touched = write_safely(project, change_id, lambda: _implement_answer(services, project, context))
    if "_contract_error" in response:
        # The work on disk stays; the malformed answer costs this attempt and is fed back as a failure.
        state["failures"].append({"attempt": state["attempt"],
                                  "contract": {"status": "INVALID", "message": response["_contract_error"]}})
        save_run(project, state)
        return None
    validate_agent_summary(response)
    if response.get("design"):
        state["design"] = response["design"]
    if response["questions"]:
        return pause(project, state, "NEEDS_INPUT", questions=response["questions"])
    boundary = audit_project_changes(project, change_id)
    require_git_visible(touched, boundary)
    if boundary["violations"]:
        return pause_for_boundary(project, state, boundary)
    uncharacterized = uncharacterized_paths(project, state, boundary, config)
    if uncharacterized:
        # Existing behaviour must be locked before it is changed: restore, lock, and try again.
        _git(project, "checkout", state["contract_commit"], "--", *uncharacterized)
        paused = characterize_paths(project, change, state, services, config, extend=uncharacterized)
        if paused is not None:
            return paused
        state["failures"].append({"attempt": state["attempt"],
                                  "characterization": {"status": "FAILED", "uncharacterized": uncharacterized}})
        save_run(project, state)
        return None
    layers, boundary = _run_checks_safely(project, change_id, config, boundary)
    layers = check_layers(layers, state.get("oracle"))
    if boundary["violations"]:
        return pause_for_boundary(project, state, boundary)
    if not layers_green(layers):
        state["failures"].append({"attempt": state["attempt"], **layers})
        save_run(project, state)
        return None
    review = _perform_review(project, change, state, services, boundary, layers)
    verdict = validate_review(review)
    if verdict == "needs_input":
        return pause(project, state, "NEEDS_INPUT", questions=review["questions"])
    if verdict == "fail":
        state["failures"].append({"attempt": state["attempt"], "review": review})
        save_run(project, state)
        return None
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
        "message": str(error)[:600], "at": moment})
    state["attempt"] = max(int(state["attempt"]) - 1, 0)
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


def _resume_manual_acceptance(project: Path, change: dict, state: dict) -> dict:
    """Continue a paused run once the human accepted the manual criteria on unchanged code."""
    require(implementation_digest(project) == state.get("candidate_sha256"),
            "Workspace changed while waiting for manual acceptance; use `storyloop reset` to retry")
    verified = state.get("verified")
    require(isinstance(verified, dict) and set(verified) == {"boundary", "layers", "review"},
            "Paused run lacks its verification evidence")
    paused = _manual_acceptance_gate(project, change, state, verified)
    if paused is not None:
        return paused
    return complete_change(project, change, state, **verified)


def _prepare_acceptance(
    project: Path, change: dict, services: LoopServices, state: dict,
) -> dict | None:
    change_id = change["change_id"]
    contract_root = materialize_approved_change(project, change_id)
    state.setdefault("openspec", {})["delta"] = validate_with_cli(project, "change", change_id)
    smoke_state = protection_path(project, change_id)
    if smoke_state.exists():
        load_smoke_manifest(project, change_id)
    else:
        generated = read_only_call(
            project, change_id, lambda: services.generate_smoke(project, change)
        )
        require(isinstance(generated, dict) and set(generated) == {"files", "manifest"},
                "Smoke agent returned an invalid contract")
        install_smoke_suite(project, change_id, without_manifest_echo(generated["files"]), generated["manifest"])
    smoke_root = acceptance_suite_root(project, change_id)
    paths = relative_files(project, [contract_root, smoke_root])
    if state["status"] == "PREPARING":
        state["status"] = "CONTRACT_READY"
        save_run(project, state)
    if state["status"] == "CONTRACT_READY":
        state.update({"status": "CONTRACT_COMMITTED",
                      "contract_commit": _ensure_contract_commit(project, state, paths)})
        save_run(project, state)
    try:
        red = _ensure_red_evidence(project, change_id, state["contract_commit"])
    except StoryLoopError as exc:
        if exc.code not in {"EXISTING_GREEN", "ORACLE_DEFECT"}:
            raise
        return pause(project, state, exc.code, error=str(exc),
                     acceptance={"status": exc.code, **exc.details})
    ensure_baseline(project, state, change["product"])  # sampled once, on the code before implementation
    for key in ("acceptance", "error", "next"):
        state.pop(key, None)
    state.update({"status": "IMPLEMENTING",
                  "red_evidence_sha256": red["evidence_sha256"]})
    save_run(project, state)
    return None


def _ensure_contract_commit(project: Path, state: dict, paths: list[str]) -> str:
    status = _git(project, "status", "--porcelain=v1", "--untracked-files=all").strip()
    if status:
        result = commit_paths(
            project, paths,
            f"test({state['change_id']}): lock approved contract and smoke acceptance",
        )
        return result["commit"]
    head = _git(project, "rev-parse", "HEAD").strip()
    parents = _git(project, "rev-list", "--parents", "-n", "1", head).split()
    changed = set(filter(None, _git(
        project, "diff-tree", "--no-commit-id", "--name-only", "-r", "-z", head
    ).split("\0")))
    require(len(parents) == 2 and parents[1] == state["source_head"]
            and changed == set(paths), "Cannot recover the contract commit safely")
    return head


def _ensure_red_evidence(project: Path, change_id: str, contract_commit: str) -> dict:
    """RED is proven on the contract commit plus the current suite: never on the workspace, which after a
    stop may already hold implementation edits that would make the new behaviour look present."""
    path = project / ".storyloop/evidence" / change_id / RED_EVIDENCE
    if path.exists():
        value = load_json(path)
        require(value.get("change_id") == change_id and value.get("status") == "RED",
                "Stored RED evidence is invalid")
        return {**value, "evidence_path": str(path),
                "evidence_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    return read_only_call(
        project, change_id,
        lambda: run_smoke_isolated(project, change_id, expect_red=True, at_commit=contract_commit),
        allowed_evidence=RED_EVIDENCE,
    )


LAYER_ORDER = ("unit", "integration", "acceptance", "smoke", "checks")


def layers_green(layers: dict) -> bool:
    """Acceptance must pass; every other layer passes or is skipped because it is not configured."""
    return (layers["acceptance"]["status"] == "PASSED"
            and all(layers[name]["status"] in {"PASSED", "SKIPPED"} for name in LAYER_ORDER if name != "acceptance"))


def _run_checks_safely(
    project: Path, change_id: str, config: dict, prior_boundary: dict,
) -> tuple[dict, dict]:
    """Every verification layer, in order, each with its own verdict: unit, integration, acceptance, smoke, checks."""
    before = capture_snapshot(project, include_control=True, include_ignored=True)
    control = control_digest(project, change_id, excluded_evidence=RESULT_EVIDENCE)
    profile, timeout = config["profile"], config["check_timeout_seconds"]
    try:
        layers = {
            "unit": run_commands_isolated(project, layer_commands(profile, "unit"), timeout),
            "integration": run_commands_isolated(project, layer_commands(profile, "integration"), timeout),
            "acceptance": run_smoke_isolated(project, change_id),
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


def _perform_review(
    project: Path, change: dict, state: dict, services: LoopServices,
    boundary: dict, layers: dict,
) -> dict:
    context = {"change": change, "attempt": state["attempt"],
               "layers": layers, "boundary": boundary, "design_plan": design_plan(state),
               "answers": load_answers(project, change["change_id"]),
               "design": state.get("design") or ""}
    try:
        return read_only_call(
            project, change["change_id"], lambda: services.review(project, context)
        )
    except StoryLoopError as exc:
        if exc.code == "AGENT_UNAVAILABLE":
            exc.details.setdefault("role", "review")
        raise


def _new_run_state(change_id: str, started: dict, contract_digest: str) -> dict:
    return {"version": 1, "change_id": change_id, "status": "PREPARING", "attempt": 0,
            "feature_branch": started["branch"], "source_branch": started["source_branch"],
            "source_head": started["source_head"], "contract_digest": contract_digest,
            "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "failures": []}


def _default_services() -> LoopServices:
    from .project_agents import invoke_project_agent
    return LoopServices(
        generate_smoke=lambda project, state: invoke_project_agent(
            project, "acceptance",
            {"change": state, "suite_root": suite_relative(project, state["change_id"])}),
        implement=lambda project, context: invoke_project_agent(project, "implement", context),
        review=lambda project, context: invoke_project_agent(project, "review", context),
        characterize=lambda project, context: invoke_project_agent(project, "characterize", context),
        design=lambda project, context: invoke_project_agent(project, "design", context),
    )
