"""External autonomous loop for approved product changes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from typing import Callable

from .approvals import materialize_approved_change
from .baseline import (_git, advance_repository_head, load_project_config,
                       verify_baseline)
from .boundaries import capture_snapshot, snapshot_changes
from .change_report import generate_change_report
from .changes import load_change
from .models import StoryLoopError, require
from .project_boundaries import audit_project_changes
from .answers import load_answers, _pending as _unanswered
from .telemetry import summarize
from .manual_acceptance import load_manual_acceptance, pending_manual_criteria
from .project_contracts import (validate_agent_summary,
                                validate_review)
from .project_git import commit_paths, current_branch, finish_feature, start_feature
from .project_loop_support import (control_digest as _control_digest,
                                   relative_files as _relative_files,
                                   run_path as _run_path, save_run as _save_run,
                                   set_change_status as _set_change_status)
from .project_receipt import (ensure_merge_receipt, recover_completed_merge,
                              verify_verified_artifacts)
from .smoke import install_smoke_suite, load_smoke_manifest
from .smoke_runner import git_visible_digest, run_commands_isolated, run_smoke_isolated
from .storage import load_json, locked


@dataclass(frozen=True)
class LoopServices:
    """Agent operations kept injectable so the controller remains authoritative."""

    generate_smoke: Callable[[Path, dict], dict]
    implement: Callable[[Path, dict], dict]
    review: Callable[[Path, dict], dict]


def run_change(project: Path, change_id: str, services: LoopServices | None = None) -> dict:
    """Run or resume an approved change until pass or an explicit pause state."""
    with locked(Path(project).resolve()):
        return _run_change_locked(Path(project).resolve(), change_id, services)


def _run_change_locked(
    project: Path, change_id: str, services: LoopServices | None,
) -> dict:
    project = Path(project).resolve()
    change = load_change(project, change_id)
    saved_path = _run_path(project, change_id)
    saved = load_json(saved_path) if saved_path.exists() else None
    if saved and saved.get("status") == "PASSED":
        require(change["status"] == "PASSED", "Completed run has inconsistent change state")
        return _passed_result(saved)
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
        return _finish_verified(project, change, state)
    if state["status"] == "MANUAL_ACCEPTANCE_REQUIRED":
        return _resume_manual_acceptance(project, change, state)
    if state["status"] == "MERGED":
        return _finish_merged(project, change, state)
    if state["status"] == "RECEIPTING":
        return _finish_receipting(project, change, state)
    if state["status"] == "FINALIZING":
        return _finalize_change(project, change, state)
    if fresh or state["status"] in {"PREPARING", "CONTRACT_READY", "CONTRACT_COMMITTED"}:
        _prepare_acceptance(project, change, services, state)
    for attempt in range(start_attempt, config["max_attempts"] + 1):
        state["attempt"] = attempt
        state["status"] = "IMPLEMENTING"
        _save_run(project, state)
        try:
            outcome = _run_attempt(project, change, state, services, config)
        except StoryLoopError as exc:
            if exc.code == "WORKSPACE_TAINTED":
                state.update({"status": "WORKSPACE_TAINTED", "error": str(exc)})
                _save_run(project, state)
            raise
        if outcome is not None:
            return outcome
    return _pause(project, state, "BUDGET_EXHAUSTED")


def load_run_state(project: Path, change_id: str) -> dict:
    return load_json(_run_path(Path(project).resolve(), change_id))


def _open_run(project: Path, change: dict) -> tuple[dict, int, bool]:
    change_id = change["change_id"]
    path = _run_path(project, change_id)
    if path.exists():
        state = load_json(path)
        resumable = {"PREPARING", "CONTRACT_READY", "CONTRACT_COMMITTED",
                     "IMPLEMENTING", "BASE_CHANGE_REQUIRED",
                     "BOUNDARY_VIOLATION", "NEEDS_INPUT", "BUDGET_EXHAUSTED",
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
            _save_run(project, state)
        expected = (state.get("source_branch")
                    if state["status"] in {"MERGED", "RECEIPTING", "FINALIZING"}
                    else state.get("feature_branch"))
        require(current == expected, "Resume the saved StoryLoop branch")
        if state["status"] != "RECEIPTING":
            verify_baseline(
                project, resume=state["status"] not in {"MERGED", "FINALIZING"}
            )
        return state, int(state.get("attempt", 0)) + 1, False
    verify_baseline(project)
    state = _new_run_state(change_id, start_feature(project, change_id),
                           change["approval"]["contract_digest"])
    _save_run(project, state)
    return state, 1, True


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
               "failures": list(state["failures"]), "answers": answers}
    response, touched = _implement_safely(project, change_id, services, context)
    validate_agent_summary(response)
    if response["questions"]:
        return _pause(project, state, "NEEDS_INPUT", questions=response["questions"])
    boundary = audit_project_changes(project, change_id)
    _require_git_visible(touched, boundary)
    if boundary["violations"]:
        return _pause_for_boundary(project, state, boundary)
    regression, smoke, boundary = _run_checks_safely(
        project, change_id, config, boundary
    )
    if boundary["violations"]:
        return _pause_for_boundary(project, state, boundary)
    if regression["status"] not in {"PASSED", "SKIPPED"} or smoke["status"] != "PASSED":
        state["failures"].append({"attempt": state["attempt"],
                                  "regression": regression, "smoke": smoke})
        _save_run(project, state)
        return None
    review = _perform_review(project, change, state, services,
                             boundary, regression, smoke)
    verdict = validate_review(review)
    if verdict == "needs_input":
        return _pause(project, state, "NEEDS_INPUT", questions=review["questions"])
    if verdict == "fail":
        state["failures"].append({"attempt": state["attempt"], "review": review})
        _save_run(project, state)
        return None
    verified = {"boundary": boundary, "regression": regression, "smoke": smoke, "review": review}
    paused = _manual_acceptance_gate(project, change, state, verified)
    if paused is not None:
        return paused
    return _complete(project, change, state, **verified)


def _manual_acceptance_gate(project: Path, change: dict, state: dict, verified: dict) -> dict | None:
    """Pause before the pass report until every manual criterion is accepted on this exact code."""
    digest = git_visible_digest(project)
    records = load_manual_acceptance(project, change["change_id"])
    pending = pending_manual_criteria(change, records, digest)
    if not pending:
        return None
    return _pause(project, state, "MANUAL_ACCEPTANCE_REQUIRED", pending=pending,
                  candidate_sha256=digest, verified=verified,
                  next=f"storyloop accept <project> --change {change['change_id']} "
                       "--criterion <ID> --note <what you verified>, then storyloop run")


def _resume_manual_acceptance(project: Path, change: dict, state: dict) -> dict:
    """Continue a paused run once the human accepted the manual criteria on unchanged code."""
    require(git_visible_digest(project) == state.get("candidate_sha256"),
            "Workspace changed while waiting for manual acceptance; use `storyloop reset` to retry")
    verified = state.get("verified")
    require(isinstance(verified, dict) and set(verified) == {"boundary", "regression", "smoke", "review"},
            "Paused run lacks its verification evidence")
    paused = _manual_acceptance_gate(project, change, state, verified)
    if paused is not None:
        return paused
    return _complete(project, change, state, **verified)


def _prepare_acceptance(
    project: Path, change: dict, services: LoopServices, state: dict,
) -> None:
    change_id = change["change_id"]
    contract_root = materialize_approved_change(project, change_id)
    smoke_state = project / ".storyloop/state/smoke" / f"{change_id}.json"
    if smoke_state.exists():
        load_smoke_manifest(project, change_id)
    else:
        generated = _read_only_call(
            project, change_id, lambda: services.generate_smoke(project, change)
        )
        require(isinstance(generated, dict) and set(generated) == {"files", "manifest"},
                "Smoke agent returned an invalid contract")
        install_smoke_suite(project, change_id, generated["files"], generated["manifest"])
    smoke_root = project / "acceptance" / "smoke" / change_id
    paths = _relative_files(project, [contract_root, smoke_root])
    if state["status"] == "PREPARING":
        state["status"] = "CONTRACT_READY"
        _save_run(project, state)
    if state["status"] == "CONTRACT_READY":
        state.update({"status": "CONTRACT_COMMITTED",
                      "contract_commit": _ensure_contract_commit(project, state, paths)})
        _save_run(project, state)
    red = _ensure_red_evidence(project, change_id)
    state.update({"status": "IMPLEMENTING",
                  "red_evidence_sha256": red["evidence_sha256"]})
    _save_run(project, state)


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


def _ensure_red_evidence(project: Path, change_id: str) -> dict:
    path = project / ".storyloop/evidence" / change_id / "smoke-red.json"
    if path.exists():
        value = load_json(path)
        require(value.get("change_id") == change_id and value.get("status") == "RED",
                "Stored RED evidence is invalid")
        return {**value, "evidence_path": str(path),
                "evidence_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    return _read_only_call(
        project, change_id,
        lambda: run_smoke_isolated(project, change_id, expect_red=True),
        allowed_evidence="smoke-red.json",
    )


def _run_checks_safely(
    project: Path, change_id: str, config: dict, prior_boundary: dict,
) -> tuple[dict, dict, dict]:
    before = capture_snapshot(project, include_control=True, include_ignored=True)
    control = _control_digest(project, change_id, excluded_evidence="smoke-result.json")
    try:
        regression = run_commands_isolated(
            project, config["profile"]["test_commands"],
            config["check_timeout_seconds"],
        )
        smoke = run_smoke_isolated(project, change_id)
    finally:
        _verify_read_only_snapshot(
            before, capture_snapshot(project, include_control=True, include_ignored=True),
            f".storyloop/evidence/{change_id}/smoke-result.json",
        )
        _require_control_unchanged(
            _control_digest(project, change_id,
                            excluded_evidence="smoke-result.json") == control,
            "Test command modified Git or the protected control plane",
        )
    boundary = audit_project_changes(project, change_id)
    require(prior_boundary["allowed"] == boundary["allowed"] or boundary["violations"],
            "Audited implementation changed during verification")
    return regression, smoke, boundary


def _perform_review(
    project: Path, change: dict, state: dict, services: LoopServices,
    boundary: dict, regression: dict, smoke: dict,
) -> dict:
    context = {"change": change, "attempt": state["attempt"],
               "regression": regression, "smoke": smoke, "boundary": boundary,
               "answers": load_answers(project, change["change_id"])}
    return _read_only_call(
        project, change["change_id"], lambda: services.review(project, context)
    )


def _implement_safely(
    project: Path, change_id: str, services: LoopServices,
    context: dict,
) -> tuple[dict, list]:
    before = capture_snapshot(project, include_control=True, include_ignored=True)
    control = _control_digest(project, change_id)
    staged = _staged_index(project)
    failure: BaseException | None = None
    try:
        response = services.implement(project, context)
    except BaseException as exc:
        failure = exc
    finally:
        _require_control_unchanged(
            _control_digest(project, change_id) == control,
            "Implementation agent modified the protected control plane",
        )
        _require_control_unchanged(_staged_index(project) == staged,
                                   "Implementation agent staged Git changes")
    after = capture_snapshot(project, include_control=True, include_ignored=True)
    changes = _without_scratch(snapshot_changes(before, after), _scratch_paths(project))
    if failure is not None:
        if changes:
            raise StoryLoopError(
                "Implementation failed after modifying the workspace",
                code="WORKSPACE_TAINTED",
            ) from failure
        raise failure
    return response, changes


def _read_only_call(
    project: Path, change_id: str, operation: Callable[[], object],
    allowed_evidence: str | None = None,
) -> object:
    before = capture_snapshot(project, include_control=True, include_ignored=True)
    control = _control_digest(project, change_id, excluded_evidence=allowed_evidence)
    staged = _staged_index(project)
    try:
        return operation()
    finally:
        allowed = (f".storyloop/evidence/{change_id}/{allowed_evidence}"
                   if allowed_evidence else None)
        _verify_read_only_snapshot(
            before, capture_snapshot(project, include_control=True, include_ignored=True), allowed,
            _scratch_paths(project),
        )
        _require_control_unchanged(_staged_index(project) == staged,
                                   "Read-only operation staged Git changes")
        _require_control_unchanged(
            _control_digest(project, change_id,
                            excluded_evidence=allowed_evidence) == control,
            "Read-only operation modified Git or the protected control plane",
        )


def _require_control_unchanged(condition: bool, message: str) -> None:
    if not condition:
        raise StoryLoopError(message, code="WORKSPACE_TAINTED")


def _verify_read_only_snapshot(before, after, allowed_path: str | None,
                               scratch: list[str] | None = None) -> None:
    changes = _without_scratch(snapshot_changes(before, after), scratch or [])
    unexpected = [item for item in changes
                  if item.path != allowed_path
                  and not _allowed_parent_directory(item.path, allowed_path)]
    if unexpected:
        paths = ", ".join(item.path for item in unexpected[:20])
        raise StoryLoopError(f"Read-only operation modified the project: {paths}",
                             code="WORKSPACE_TAINTED")


def _allowed_parent_directory(path: str, allowed_path: str | None) -> bool:
    return bool(path.endswith("/") and allowed_path
                and allowed_path.startswith(path))


def _scratch_paths(project: Path) -> list[str]:
    """Ignored locations (names or relative paths) agents may write, e.g. build output."""
    profile = load_project_config(project).get("profile", {})
    values = profile.get("scratch_paths", []) if isinstance(profile, dict) else []
    require(isinstance(values, list) and all(isinstance(item, str) and item for item in values),
            "scratch_paths must be a list of names or relative paths")
    return values


def _staged_index(project: Path) -> str:
    return _git(project, "diff", "--cached", "--name-status")


def _is_scratch(path: str, scratch: list[str]) -> bool:
    """Tolerated: .git/index refreshes, declared scratch names anywhere, declared relative paths."""
    normalized = path.rstrip("/")
    if normalized == ".git/index":
        return True
    # The engine's own call ledger is written while agents run; it is bookkeeping, not a change.
    if normalized == ".storyloop/state/telemetry" or normalized.startswith(".storyloop/state/telemetry/"):
        return True
    parts = normalized.split("/")
    return any(part == item for part in parts for item in scratch) or any(
        normalized == item.rstrip("/") or normalized.startswith(item.rstrip("/") + "/")
        for item in scratch if "/" in item)


def _without_scratch(changes: list, scratch: list[str]) -> list:
    """Drop tolerated file changes and directory entries whose only changes are tolerated."""
    files = [item for item in changes if not item.path.endswith("/")]
    kept = [item for item in files if not _is_scratch(item.path, scratch)]
    dropped = [item for item in files if _is_scratch(item.path, scratch)]
    directories = [item for item in changes if item.path.endswith("/")]
    result = list(kept)
    for entry in directories:
        if _is_scratch(entry.path, scratch):
            continue
        prefix = "" if entry.path == "./" else entry.path
        under_kept = any(item.path.startswith(prefix) for item in kept)
        under_dropped = any(item.path.startswith(prefix) for item in dropped) or any(
            other.path.startswith(prefix) and _is_scratch(other.path, scratch)
            for other in directories)
        if under_kept or not under_dropped:
            result.append(entry)
    return result


def _require_git_visible(touched: list, boundary: dict) -> None:
    visible = {item["path"] for key in ("allowed", "violations")
               for item in boundary[key]}
    invisible = [item.path for item in touched
                 if not item.path.endswith("/") and item.path not in visible]
    if invisible:
        raise StoryLoopError("Implementation created or modified ignored files",
                             code="WORKSPACE_TAINTED")


def _pause_for_boundary(project: Path, state: dict, boundary: dict) -> dict:
    return _pause(project, state, boundary["status"],
                  violations=boundary["violations"],
                  base_proposal=boundary["base_proposal"])


def _complete(
    project: Path, change: dict, state: dict, boundary: dict,
    regression: dict, smoke: dict, review: dict,
) -> dict:
    change_id = change["change_id"]
    final_boundary = audit_project_changes(project, change_id)
    require(final_boundary == boundary and not final_boundary["violations"],
            "Workspace changed after final boundary audit")
    changed_paths = [item["path"] for item in final_boundary["allowed"]]
    require(bool(changed_paths), "Passing implementation produced no auditable code changes")
    digest = git_visible_digest(project)
    manual = [record for record in load_manual_acceptance(project, change_id)
              if record.get("candidate_sha256") == digest]
    require(not pending_manual_criteria(change, manual, digest),
            "Manual acceptance is missing for the code being reported")
    implementation = commit_paths(
        project, changed_paths, f"feat({change_id}): implement approved product change"
    )
    telemetry = {**summarize(project, change_id), "attempts": state["attempt"],
                 "loop_ms": _loop_ms(state)}
    report_path = generate_change_report(
        project, change_id, implementation["commit"], regression, smoke, review,
        manual_acceptance=manual, telemetry=telemetry,
    )
    state["manual_acceptance"] = manual
    state["telemetry"] = telemetry
    report_commit = commit_paths(
        project, [report_path.relative_to(project).as_posix(),
                  report_path.with_suffix(".md").relative_to(project).as_posix()],
        f"docs({change_id}): add acceptance evidence report",
    )
    state.update({"status": "VERIFIED", "implementation_commit": implementation["commit"],
                  "report_commit": report_commit["commit"], "report": str(report_path),
                  "report_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
                  "smoke_evidence_sha256": smoke["evidence_sha256"], "review": review})
    _save_run(project, state)
    return _finish_verified(project, change, state)


def _finish_verified(project: Path, change: dict, state: dict) -> dict:
    _verify_verified_state(project, state)
    merged = finish_feature(project, state["feature_branch"], state["source_branch"])
    state.update({"status": "MERGED", "merge_commit": merged["merge_commit"]})
    _save_run(project, state)
    return _finish_merged(project, change, state)


def _finish_merged(project: Path, change: dict, state: dict) -> dict:
    require(current_branch(project) == state["source_branch"],
            "Merged run must finish on its source branch")
    require(_git(project, "rev-parse", "HEAD").strip() == state["merge_commit"],
            "Source branch changed after verified merge")
    state["status"] = "RECEIPTING"
    _save_run(project, state)
    return _finish_receipting(project, change, state)


def _finish_receipting(project: Path, change: dict, state: dict) -> dict:
    integration = ensure_merge_receipt(project, state)
    advance_repository_head(project, integration)
    state.update({"status": "FINALIZING", "integration_commit": integration,
                  "report_sha256": hashlib.sha256(Path(state["report"]).read_bytes()).hexdigest()})
    _save_run(project, state)
    return _finalize_change(project, change, state)


def _finalize_change(project: Path, change: dict, state: dict) -> dict:
    require(current_branch(project) == state["source_branch"]
            and _git(project, "rev-parse", "HEAD").strip() == state["integration_commit"],
            "Integrated source branch changed before finalization")
    advance_repository_head(project, state["integration_commit"])
    if change["status"] != "PASSED":
        _set_change_status(project, change, "PASSED")
    state["status"] = "PASSED"
    _save_run(project, state)
    return _passed_result(state)


def _loop_ms(state: dict) -> int:
    started = state.get("started_at")
    if not isinstance(started, str):
        return 0
    try:
        begun = datetime.fromisoformat(started)
    except ValueError:
        return 0
    return max(int((datetime.now(timezone.utc) - begun).total_seconds() * 1000), 0)


def _passed_result(state: dict) -> dict:
    return {"status": "PASSED", "change_id": state["change_id"],
            "attempts": state["attempt"],
            "implementation_commit": state["implementation_commit"],
            "report_commit": state["report_commit"], "merge_commit": state["merge_commit"],
            "integration_commit": state["integration_commit"],
            "branch": state["source_branch"], "report": state["report"]}


def _verify_verified_state(project: Path, state: dict) -> None:
    require(not _git(project, "status", "--porcelain=v1", "--untracked-files=all").strip(),
            "Verified feature worktree must remain clean")
    require(current_branch(project) == state["feature_branch"],
            "Verified feature branch is not checked out")
    require(_git(project, "rev-parse", state["feature_branch"]).strip() == state["report_commit"],
            "Verified feature branch changed after reporting")
    require(_git(project, "rev-parse", state["source_branch"]).strip() == state["source_head"],
            "Source branch changed after the feature run started")
    verify_verified_artifacts(project, state)


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
            project, "smoke",
            {"change": state, "suite_root": f"acceptance/smoke/{state['change_id']}"}),
        implement=lambda project, context: invoke_project_agent(project, "implement", context),
        review=lambda project, context: invoke_project_agent(project, "review", context),
    )


def _pause(project: Path, state: dict, status: str, **details) -> dict:
    state["status"] = status
    state.update(details)
    _save_run(project, state)
    return {"status": status, "change_id": state["change_id"], **details}
