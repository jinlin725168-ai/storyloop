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
from .models import StoryLoopError, identifier, require, text
from .project_boundaries import audit_project_changes
from .answers import load_answers, _pending as _unanswered
from .telemetry import summarize
from .openspec_docs import capability_of, merge_into_main_spec, render_decisions, validate_with_cli
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
from .project_profile import layer_commands, matches_test_root
from .system_smoke import run_system_smoke_isolated
from .smoke import RED_EVIDENCE, RESULT_EVIDENCE, install_smoke_suite, load_smoke_manifest, protection_path
from .smoke import suite_relative, suite_root as acceptance_suite_root
from .smoke_runner import implementation_digest, run_commands_isolated, run_smoke_isolated
from .storage import load_json, locked, write_json


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
    paused = _ensure_design(project, change, state, services) or \
        _ensure_characterization(project, change, state, services, config)
    if paused is not None:
        return paused
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


REPAIRABLE = {"IMPLEMENTING", "BUDGET_EXHAUSTED", "NEEDS_INPUT"}


def repair_smoke(project: Path, change_id: str, reason: str, services: LoopServices | None = None,
                 now=None) -> dict:
    """Let the smoke owner fix test defects in its own suite, then re-prove RED at the contract commit.

    The implementer never touches the suite; a human must judge that the failure is a test
    defect and say why. The reason is recorded, the old attempts are voided, and the new suite
    must still fail on the tree as it was before any implementation.
    """
    project = Path(project).resolve()
    change_id = identifier(change_id, "change id")
    reason = text(reason, "repair reason")
    change = load_change(project, change_id)
    path = _run_path(project, change_id)
    require(path.exists(), "Repair needs a started run with a committed contract")
    state = load_json(path)
    require(state.get("status") in REPAIRABLE and state.get("contract_commit"),
            f"Smoke repair is only possible while implementing (current: {state.get('status')})")
    require(current_branch(project) == state["feature_branch"], "Check out the run's feature branch first")
    services = services or _default_services()
    suite_root = acceptance_suite_root(project, change_id)
    existing = [{"path": p.relative_to(suite_root).as_posix(), "content": p.read_text(encoding="utf-8")}
                for p in sorted(suite_root.rglob("*")) if p.is_file() and p.name != "manifest.json"]
    evidence = _last_smoke_evidence(project, change_id, state)
    request = {**change, "repair": {"reason": reason, "existing_files": existing,
                                    "manifest": load_json(suite_root / "manifest.json"),
                                    "failure_evidence": evidence}}
    generated = _read_only_call(project, change_id, lambda: services.generate_smoke(project, request))
    require(isinstance(generated, dict) and set(generated) == {"files", "manifest"},
            "Smoke agent returned an invalid contract")
    files = [item for item in generated["files"]
             if not (isinstance(item, dict) and item.get("path") == "manifest.json")]  # manifest travels separately
    install_smoke_suite(project, change_id, files, generated["manifest"], replace=True)
    for name in (RED_EVIDENCE, RESULT_EVIDENCE):
        (project / ".storyloop/evidence" / change_id / name).unlink(missing_ok=True)
    paths = _relative_files(project, [suite_root])
    commit = commit_paths(project, paths, f"test({change_id}): repair smoke suite")
    red = _read_only_call(
        project, change_id,
        lambda: run_smoke_isolated(project, change_id, expect_red=True, at_commit=state["contract_commit"]),
        allowed_evidence=RED_EVIDENCE,
    )
    moment = (now or (lambda: datetime.now(timezone.utc)))().astimezone(timezone.utc).isoformat(timespec="seconds")
    state.setdefault("repairs", []).append({"at": moment, "reason": reason, "commit": commit["commit"],
                                            "voided_attempts": int(state.get("attempt", 0))})
    for key in ("questions", "error", "pending"):
        state.pop(key, None)
    state.update({"status": "IMPLEMENTING", "attempt": 0, "red_evidence_sha256": red["evidence_sha256"]})
    _save_run(project, state)
    return {"status": "SMOKE_REPAIRED", "change_id": change_id, "commit": commit["commit"],
            "next": f"storyloop run <project> --change {change_id}"}


def _last_smoke_evidence(project: Path, change_id: str, state: dict) -> dict:
    """Tails of the latest smoke output, so the repairing agent sees what actually failed."""
    for failure in reversed(state.get("failures") or []):
        smoke = failure.get("acceptance") or failure.get("smoke")
        if isinstance(smoke, dict) and smoke.get("results"):
            return {"status": smoke.get("status"), "results": [
                {"id": r.get("id"), "status": r.get("status"), "stdout": str(r.get("stdout", ""))[-6000:],
                 "stderr": str(r.get("stderr", ""))[-6000:]} for r in smoke["results"]]}
    return {}


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
               "failures": list(state["failures"]), "answers": answers,
               "characterization": state.get("characterization"),
               "design_plan": _design_plan(state)}
    response, touched = _write_safely(project, change_id, lambda: services.implement(project, context))
    validate_agent_summary(response)
    if response.get("design"):
        state["design"] = response["design"]
    if response["questions"]:
        return _pause(project, state, "NEEDS_INPUT", questions=response["questions"])
    boundary = audit_project_changes(project, change_id)
    _require_git_visible(touched, boundary)
    if boundary["violations"]:
        return _pause_for_boundary(project, state, boundary)
    uncharacterized = _uncharacterized_paths(project, state, boundary, config)
    if uncharacterized:
        # Existing behaviour must be locked before it is changed: restore, lock, and try again.
        _git(project, "checkout", state["contract_commit"], "--", *uncharacterized)
        paused = _characterize_paths(project, change, state, services, config, extend=uncharacterized)
        if paused is not None:
            return paused
        state["failures"].append({"attempt": state["attempt"],
                                  "characterization": {"status": "FAILED", "uncharacterized": uncharacterized}})
        _save_run(project, state)
        return None
    layers, boundary = _run_checks_safely(project, change_id, config, boundary)
    if boundary["violations"]:
        return _pause_for_boundary(project, state, boundary)
    if not layers_green(layers):
        state["failures"].append({"attempt": state["attempt"], **layers})
        _save_run(project, state)
        return None
    review = _perform_review(project, change, state, services, boundary, layers)
    verdict = validate_review(review)
    if verdict == "needs_input":
        return _pause(project, state, "NEEDS_INPUT", questions=review["questions"])
    if verdict == "fail":
        state["failures"].append({"attempt": state["attempt"], "review": review})
        _save_run(project, state)
        return None
    verified = {"boundary": boundary, "layers": layers, "review": review}
    paused = _manual_acceptance_gate(project, change, state, verified)
    if paused is not None:
        return paused
    return _complete(project, change, state, **verified)


def _manual_acceptance_gate(project: Path, change: dict, state: dict, verified: dict) -> dict | None:
    """Pause before the pass report until every manual criterion is accepted on this exact code."""
    digest = implementation_digest(project)
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
    require(implementation_digest(project) == state.get("candidate_sha256"),
            "Workspace changed while waiting for manual acceptance; use `storyloop reset` to retry")
    verified = state.get("verified")
    require(isinstance(verified, dict) and set(verified) == {"boundary", "layers", "review"},
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
    state.setdefault("openspec", {})["delta"] = validate_with_cli(project, "change", change_id)
    smoke_state = protection_path(project, change_id)
    if smoke_state.exists():
        load_smoke_manifest(project, change_id)
    else:
        generated = _read_only_call(
            project, change_id, lambda: services.generate_smoke(project, change)
        )
        require(isinstance(generated, dict) and set(generated) == {"files", "manifest"},
                "Smoke agent returned an invalid contract")
        install_smoke_suite(project, change_id, generated["files"], generated["manifest"])
    smoke_root = acceptance_suite_root(project, change_id)
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
    path = project / ".storyloop/evidence" / change_id / RED_EVIDENCE
    if path.exists():
        value = load_json(path)
        require(value.get("change_id") == change_id and value.get("status") == "RED",
                "Stored RED evidence is invalid")
        return {**value, "evidence_path": str(path),
                "evidence_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    return _read_only_call(
        project, change_id,
        lambda: run_smoke_isolated(project, change_id, expect_red=True),
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
    control = _control_digest(project, change_id, excluded_evidence=RESULT_EVIDENCE)
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
        _verify_read_only_snapshot(
            before, capture_snapshot(project, include_control=True, include_ignored=True),
            f".storyloop/evidence/{change_id}/{RESULT_EVIDENCE}",
        )
        _require_control_unchanged(
            _control_digest(project, change_id,
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
               "layers": layers, "boundary": boundary, "design_plan": _design_plan(state),
               "answers": load_answers(project, change["change_id"]),
               "design": state.get("design") or ""}
    return _read_only_call(
        project, change["change_id"], lambda: services.review(project, context)
    )


def _write_safely(project: Path, change_id: str, operation: Callable[[], dict]) -> tuple[dict, list]:
    """Run an agent that may write the workspace; the control plane and the index must stay untouched."""
    before = capture_snapshot(project, include_control=True, include_ignored=True)
    control = _control_digest(project, change_id)
    staged = _staged_index(project)
    failure: BaseException | None = None
    try:
        response = operation()
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


def _design_plan(state: dict) -> dict | None:
    plan = state.get("design_plan") or {}
    if plan.get("status") != "DONE":
        return None
    return {"summary": plan["summary"], "touched": list(plan["touched"]), "design": plan["design"]}


def _ensure_design(project: Path, change: dict, state: dict, services: LoopServices) -> dict | None:
    """The architect step, once per change after RED: a read-only plan naming the files to modify.

    Its `touched` list seeds the characterization gate and its Markdown is what implement follows
    and review checks against. Design questions pause the run like any other agent question.
    """
    current = state.get("design_plan") or {}
    if current.get("status") in {"DONE", "SKIPPED"}:
        return None
    if services.design is None:
        state["design_plan"] = {"status": "SKIPPED", "reason": "no design service", "touched": [], "design": ""}
        _save_run(project, state)
        return None
    change_id = change["change_id"]
    context = {"change": change, "answers": load_answers(project, change_id)}
    response = _read_only_call(project, change_id, lambda: services.design(project, context))
    require(isinstance(response, dict) and isinstance(response.get("summary"), str)
            and isinstance(response.get("touched"), list) and all(isinstance(p, str) for p in response["touched"])
            and isinstance(response.get("design"), str) and isinstance(response.get("questions"), list)
            and all(isinstance(q, str) and q.strip() for q in response["questions"]),
            "Design agent returned an invalid result")
    if response["questions"]:
        return _pause(project, state, "NEEDS_INPUT", questions=response["questions"])
    require(response["design"].strip(), "Design must contain the plan as Markdown")
    touched = sorted(set(response["touched"]))
    for path in touched:
        require(_exists_at(project, state["contract_commit"], path),
                f"Design names a file that does not exist at the contract commit: {path}")
    state["design_plan"] = {"status": "DONE", "summary": response["summary"], "touched": touched,
                            "design": response["design"].rstrip("\n") + "\n"}
    state.pop("questions", None)
    _save_run(project, state)
    return None


def _ensure_characterization(project: Path, change: dict, state: dict, services: LoopServices,
                             config: dict) -> dict | None:
    """Layer 3 gate, once per change: lock the current behaviour of the paths about to change."""
    current = state.get("characterization") or {}
    if current.get("status") in {"GREEN", "SKIPPED"}:
        return None
    profile = config["profile"]
    reason = None
    if not profile.get("test_roots"):
        reason = "test_roots not configured"
    elif not layer_commands(profile, "unit"):
        reason = "unit_commands not configured"
    elif services.characterize is None:
        reason = "no characterize service"
    if reason:
        state["characterization"] = {"status": "SKIPPED", "reason": reason, "touched": [], "tests": []}
        _save_run(project, state)
        return None
    return _characterize_paths(project, change, state, services, config, extend=None)


def _characterize_paths(project: Path, change: dict, state: dict, services: LoopServices, config: dict,
                        extend: list[str] | None) -> dict | None:
    change_id = change["change_id"]
    profile = config["profile"]
    previous = state.get("characterization") or {"touched": [], "tests": []}
    plan = _design_plan(state)
    context = {"change": change, "test_roots": profile["test_roots"], "extend": list(extend or []),
               "characterization": previous if previous.get("status") == "GREEN" else None,
               "design": plan}
    response, written = _write_safely(project, change_id, lambda: services.characterize(project, context))
    require(isinstance(response, dict) and isinstance(response.get("summary"), str)
            and isinstance(response.get("touched"), list) and isinstance(response.get("tests"), list)
            and all(isinstance(p, str) for p in response["touched"] + response["tests"]),
            "Characterization agent returned an invalid result")
    require(not response.get("questions"), "Characterization asks no questions; it locks what exists")
    boundary = audit_project_changes(project, change_id)
    _require_git_visible(written, boundary)
    # Judge only what this call wrote: in extend mode the implementation's own edits are already present.
    changed = sorted({item.path for item in written if not item.path.endswith("/")})
    violating = {item["path"] for item in boundary["violations"]}
    outside = [path for path in changed if path in violating or not matches_test_root(path, profile["test_roots"])]
    if outside:
        violations = [{"path": p, "category": "NOT_A_TEST_PATH"} for p in outside]
        return _pause(project, state, "BOUNDARY_VIOLATION", violations=violations, base_proposal=None,
                      error="Characterization may only write test files")
    touched = sorted(set(previous.get("touched", [])) | set(response["touched"]) | set(extend or [])
                     | set(plan["touched"] if plan else []))
    for path in touched:
        require(_exists_at(project, state["contract_commit"], path) and not matches_test_root(path, profile["test_roots"]),
                f"Characterized path must be an existing non-test file: {path}")
    unit = run_commands_isolated(project, layer_commands(profile, "unit"), config["check_timeout_seconds"])
    if unit["status"] != "PASSED":
        return _pause(project, state, "CHARACTERIZATION_FAILED", unit=unit,
                      error="Characterization tests must be green on the current code before implementation",
                      next="Fix the characterization tests (they lock existing behaviour), then storyloop run")
    commits = list(previous.get("commits", []))
    if changed:
        commits.append(commit_paths(project, changed, f"test({change_id}): characterize touched paths")["commit"])
    state["characterization"] = {"status": "GREEN", "touched": touched,
                                 "tests": sorted(set(previous.get("tests", [])) | set(response["tests"]) | set(changed)),
                                 "commits": commits}
    _save_run(project, state)
    return None


def _uncharacterized_paths(project: Path, state: dict, boundary: dict, config: dict) -> list[str]:
    """Pre-existing non-test files the implementation modified without a characterization lock."""
    characterization = state.get("characterization") or {}
    if characterization.get("status") != "GREEN":
        return []
    roots = config["profile"].get("test_roots") or []
    touched = set(characterization.get("touched", []))
    return sorted(item["path"] for item in boundary["allowed"]
                  if item["path"] not in touched and not matches_test_root(item["path"], roots)
                  and _exists_at(project, state["contract_commit"], item["path"]))


def _exists_at(project: Path, commit: str, path: str) -> bool:
    try:
        _git(project, "cat-file", "-e", f"{commit}:{path}")
        return True
    except Exception:  # noqa: BLE001  (git reports a missing object through a non-zero exit)
        return False


def _pause_for_boundary(project: Path, state: dict, boundary: dict) -> dict:
    return _pause(project, state, boundary["status"],
                  violations=boundary["violations"],
                  base_proposal=boundary["base_proposal"])


def _complete(
    project: Path, change: dict, state: dict, boundary: dict,
    layers: dict, review: dict,
) -> dict:
    change_id = change["change_id"]
    subject = f"feat({change_id}): implement approved product change"
    # A completion that crashed after writing docs, specs, UI assets or reports left engine
    # outputs behind; discard them so this attempt regenerates everything from the same state.
    _discard_engine_outputs(project, change_id)
    final_boundary = _without_engine_outputs(audit_project_changes(project, change_id), change_id)
    boundary = _without_engine_outputs(boundary, change_id)
    digest = implementation_digest(project)
    manual = [record for record in load_manual_acceptance(project, change_id)
              if record.get("candidate_sha256") == digest]
    require(not pending_manual_criteria(change, manual, digest),
            "Manual acceptance is missing for the code being reported")
    already = _implementation_already_committed(project, subject, final_boundary)
    if already:
        implementation = {"commit": already}  # crash after the commit: adopt it, do not demand new changes
    else:
        require(final_boundary == boundary and not final_boundary["violations"],
                "Workspace changed after final boundary audit")
        changed_paths = [item["path"] for item in final_boundary["allowed"]]
        require(bool(changed_paths), "Passing implementation produced no auditable code changes")
        implementation = commit_paths(project, changed_paths, subject)
    telemetry = {**summarize(project, change_id), "attempts": state["attempt"],
                 "loop_ms": _loop_ms(state)}
    report_path = generate_change_report(
        project, change_id, implementation["commit"], layers, review,
        manual_acceptance=manual, telemetry=telemetry,
    )
    state["manual_acceptance"] = manual
    state["telemetry"] = telemetry
    documents = _write_openspec_artifacts(project, change, state, manual)
    report_commit = commit_paths(
        project, list(dict.fromkeys([report_path.relative_to(project).as_posix(),
                                     report_path.with_suffix(".md").relative_to(project).as_posix(), *documents])),
        f"docs({change_id}): add acceptance evidence report",
    )
    state.update({"status": "VERIFIED", "implementation_commit": implementation["commit"],
                  "report_commit": report_commit["commit"], "report": str(report_path),
                  "report_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
                  "acceptance_evidence_sha256": layers["acceptance"]["evidence_sha256"], "review": review})
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


def _engine_output_roots(change_id: str) -> tuple[str, ...]:
    return (f"openspec/changes/{change_id}/design.md", f"openspec/changes/{change_id}/decisions.md",
            "openspec/specs/", "acceptance/ui/", f"reports/storyloop/{change_id}/")


def _discard_engine_outputs(project: Path, change_id: str) -> None:
    """Restore tracked engine outputs and delete untracked ones (they are regenerated on completion)."""
    roots = _engine_output_roots(change_id)
    status = _git(project, "status", "--porcelain=v1", "--untracked-files=all", "-z")
    for entry in filter(None, status.split("\0")):
        code, path = entry[:2], entry[3:]
        engine_owned = any(path == root or path.startswith(root) for root in roots) or (
            path.startswith("openspec/changes/") and path.endswith("/traceability.json")
            and not path.startswith(f"openspec/changes/{change_id}/"))
        if not engine_owned:
            continue
        target = project / path
        if code.strip() == "??":
            target.unlink(missing_ok=True)
        else:
            _git(project, "checkout", "--", path)
    for root in ("acceptance/ui", f"reports/storyloop/{change_id}"):
        directory = project / root
        for empty in sorted((p for p in directory.rglob("*") if p.is_dir()), reverse=True) if directory.is_dir() else []:
            if not any(empty.iterdir()):
                empty.rmdir()


def _without_engine_outputs(boundary: dict, change_id: str) -> dict:
    roots = _engine_output_roots(change_id)
    keep = lambda item: not any(item["path"] == root or item["path"].startswith(root) for root in roots)  # noqa: E731
    allowed = [i for i in boundary["allowed"] if keep(i)]
    violations = [i for i in boundary["violations"] if keep(i)]
    status = ("CLEAN" if not allowed and not violations else "ALLOWED" if not violations
              else boundary["status"])
    return {**boundary, "allowed": allowed, "violations": violations, "status": status,
            "base_proposal": boundary.get("base_proposal") if violations else None}


def _implementation_already_committed(project: Path, subject: str, boundary: dict) -> str | None:
    """HEAD is the implementation commit of this change and nothing is left to commit."""
    if boundary["allowed"] or boundary["violations"]:
        return None
    if _git(project, "log", "-1", "--format=%s").strip() != subject:
        return None
    if _git(project, "status", "--porcelain=v1", "--untracked-files=all").strip():
        return None
    return _git(project, "rev-parse", "HEAD").strip()


def _write_openspec_artifacts(project: Path, change: dict, state: dict, manual: list[dict]) -> list[str]:
    """design.md, decisions.md and the living spec merge; returns the paths to commit."""
    change_id = change["change_id"]
    base = project / "openspec" / "changes" / change_id
    written: list[str] = []
    plan = _design_plan(state)
    parts = [plan["design"].rstrip("\n")] if plan else []
    if state.get("design"):
        parts.append(("## Implementation record\n\n" if plan else "") + state["design"].rstrip("\n"))
    if parts:
        (base / "design.md").write_text("\n\n".join(parts) + "\n", encoding="utf-8")
        written.append(f"openspec/changes/{change_id}/design.md")
    decisions = render_decisions(change, load_answers(project, change_id), manual, state.get("resets") or [],
                                 state.get("repairs") or [])
    (base / "decisions.md").write_text(decisions, encoding="utf-8")
    written.append(f"openspec/changes/{change_id}/decisions.md")
    capability = capability_of(change["product"], change_id)
    delta_path = base / "specs" / capability / "spec.md"
    if delta_path.is_file():
        main_path = project / "openspec" / "specs" / capability / "spec.md"
        current = main_path.read_text(encoding="utf-8") if main_path.is_file() else None
        merged, replaced = merge_into_main_spec(current, delta_path.read_text(encoding="utf-8"),
                                                capability, change_id)
        main_path.parent.mkdir(parents=True, exist_ok=True)
        main_path.write_text(merged, encoding="utf-8")
        written.append(f"openspec/specs/{capability}/spec.md")
        state.setdefault("openspec", {})["spec"] = validate_with_cli(project, "spec", capability)
        state["superseded"] = replaced
        written.extend(_mark_superseded(project, replaced, change_id))
    return written


def _mark_superseded(project: Path, replaced: list[dict], change_id: str) -> list[str]:
    """Record in older changes which of their scenarios the new change replaced."""
    written: list[str] = []
    for item in replaced:
        path = project / "openspec" / "changes" / item["change_id"] / "traceability.json"
        if not path.is_file():
            continue
        trace = load_json(path)
        touched = False
        names = set(item.get("superseded") or [])
        for entry in trace.get("criteria", []):
            if (entry.get("story_id") == item["story_id"] and entry.get("criterion_id") in names
                    and entry.get("superseded_by") != change_id):
                entry["superseded_by"] = change_id
                touched = True
        relative = f"openspec/changes/{item['change_id']}/traceability.json"
        if touched:
            write_json(path, trace, max_bytes=1_000_000)
        if relative not in written and (touched or _git(project, "status", "--porcelain", "--", relative).strip()):
            written.append(relative)
    return written


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
            project, "acceptance",
            {"change": state, "suite_root": suite_relative(project, state["change_id"])}),
        implement=lambda project, context: invoke_project_agent(project, "implement", context),
        review=lambda project, context: invoke_project_agent(project, "review", context),
        characterize=lambda project, context: invoke_project_agent(project, "characterize", context),
        design=lambda project, context: invoke_project_agent(project, "design", context),
    )


def _pause(project: Path, state: dict, status: str, **details) -> dict:
    state["status"] = status
    state.update(details)
    _save_run(project, state)
    return {"status": status, "change_id": state["change_id"], **details}
