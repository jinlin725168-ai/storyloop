"""Human base execution through the shared run state and transition guards."""

from __future__ import annotations

from pathlib import Path
import hashlib
import json

from .approvals import materialize_approved_change
from .base_upgrade import base_candidate_paths, establish_old_green
from .baseline import _git, load_project_config
from .base_run_open import _open_state
from .git_command import git_is_ancestor
from .manual_acceptance import load_manual_acceptance
from .models import StoryLoopError, require, text
from .project_git import current_branch, commit_paths, exact_child_commit, finish_feature
from .project_loop_support import relative_files
from .project_profile import base_commands_digest, effective_base_profile, layer_commands
from .run_state import run_path, save_run
from .run_stops import pause
from .run_transitions import transition
from .run_guards import (base_candidate_authors, trusted_base_acceptance,
                         trusted_base_evidence, trusted_base_review)
from .run_recovery import (BASE_PREMERGE, base_source_contains_candidate,
                           manual_base_merge_error, restart_base_attempt)
from .project_receipt import _dirty_paths, recover_completed_merge
from .smoke_runner import implementation_digest, run_commands_isolated
from .storage import load_json, locked, write_json, write_text_atomic
from .system_smoke import run_system_smoke_isolated


def run_base_change(project: Path, change: dict, implementer: str | None, new_run_state) -> dict:
    """Open or resume the manually implemented base change without an agent call."""
    state = _open_state(project, change, implementer, new_run_state)
    premerge = state["status"] in BASE_PREMERGE and state.get("base_resume_status") != "FINALIZING"
    merged = _resume_git_merge(project, change, state) if premerge else None
    if merged is not None:
        return merged
    reason = _premerge_restart_reason(project, state)
    if reason is not None:
        return _restart_pause(project, change, state, reason)
    if state["status"] in {"PREPARING", "CONTRACT_READY", "CONTRACT_COMMITTED"}:
        return _prepare(project, change, state)
    if state["status"] == "HUMAN_IMPLEMENTATION_REQUIRED":
        return _candidate_to_review(project, change, state)
    if state["status"] == "IMPLEMENTING":
        return _restart_pause(project, change, state, "Unfinished base verification")
    if state["status"] == "BASE_REVIEW_REQUIRED":
        return _review_stop(project, change, state)
    if state["status"] == "MANUAL_ACCEPTANCE_REQUIRED":
        return _finish_after_acceptance(project, change, state)
    if state["status"] == "VERIFIED":
        return _finish_verified(project, change, state)
    if state["status"] == "MERGED":
        return _finish_merged(project, change, state)
    if state["status"] == "RECEIPTING":
        return _base_receipt(project, change, state)
    if state["status"] == "FINALIZING":
        return _finalize_base(project, change, state)
    if (state["status"] == "WORKSPACE_TAINTED"
            and state.get("base_resume_status") == "FINALIZING"):
        transition(project, state, "FINALIZING", event="base_baseline_recheck")
        state.pop("base_resume_status", None)
        state.pop("baseline_issue", None)
        state.pop("stop", None)
        save_run(project, state)
        return _finalize_base(project, change, state)
    if state["status"] == "PASSED":
        require(change["status"] == "PASSED", "Completed base run has inconsistent change state")
        from .project_loop_finish import passed_result
        return passed_result(state)
    return {"status": state["status"], "change_id": change["change_id"]}


def _resume_git_merge(project: Path, change: dict, state: dict) -> dict | None:
    if not base_source_contains_candidate(project, state):
        return None
    if state["status"] == "VERIFIED":
        report = state.get("report_commit")
        source_ref = f"refs/heads/{state['source_branch']}"
        require(isinstance(report, str) and git_is_ancestor(project, report, source_ref),
                manual_base_merge_error(state, "source contains candidate without verified report"))
        try:
            recover_completed_merge(project, state, source_ref=source_ref)
        except StoryLoopError as exc:
            raise StoryLoopError(manual_base_merge_error(
                state, "source lacks expected verified merge")) from exc
        if current_branch(project) == state["feature_branch"]:
            require(not _dirty_paths(project), "Clean the base branch before merge recovery")
            _git(project, "switch", state["source_branch"])
        return _finish_verified(project, change, state)
    premerge = {"CONTRACT_COMMITTED", "HUMAN_IMPLEMENTATION_REQUIRED", "IMPLEMENTING",
                "BASE_REVIEW_REQUIRED", "MANUAL_ACCEPTANCE_REQUIRED"}
    require(state["status"] not in premerge
            and not (state["status"] == "WORKSPACE_TAINTED"
                     and state.get("base_resume_status") != "FINALIZING"),
            manual_base_merge_error(state, "source manually merged candidate before verification"))
    return None


def _premerge_restart_reason(project: Path, state: dict) -> str | None:
    status = state["status"]
    if status == "WORKSPACE_TAINTED" and state.get("base_resume_status") != "FINALIZING":
        return "Interrupted base attempt"
    if status == "VERIFIED" and current_branch(project) == state["feature_branch"]:
        return "Report delivery interrupted before merge"
    if status in {"CONTRACT_COMMITTED", "HUMAN_IMPLEMENTATION_REQUIRED",
                  "IMPLEMENTING", "BASE_REVIEW_REQUIRED", "MANUAL_ACCEPTANCE_REQUIRED"}:
        if state.get("base_inflight"):
            return "Base attempt interrupted before checkpoint"
    if status not in {"BASE_REVIEW_REQUIRED", "MANUAL_ACCEPTANCE_REQUIRED"}:
        return None
    if (_git(project, "rev-parse", "HEAD").strip() != state.get("candidate_commit")
            or _dirty_paths(project)):
        return "Human candidate changed before review or acceptance"
    evidence = trusted_base_evidence(project, state)
    if (evidence is None or evidence.get("status") != "PASSED"
            or evidence.get("candidate_commit") != state.get("candidate_commit")
            or evidence.get("candidate_sha256") != state.get("candidate_sha256")):
        return "Base evidence differs from the current attempt"
    review_path = _review_path(project, state["change_id"])
    if state.get("base_review") is not None or state.get("base_review_sha256") or review_path.exists():
        review = trusted_base_review(project, state)
        if (review is None or review.get("candidate_commit") != state["candidate_commit"]
                or review.get("candidate_sha256") != evidence["candidate_sha256"]):
            return "Base review differs from the current attempt"
    if status == "MANUAL_ACCEPTANCE_REQUIRED":
        acceptance = project / ".storyloop/state/acceptance" / f"{state['change_id']}.json"
        if (state.get("manual_acceptance_sha256") or acceptance.exists()) and not trusted_base_acceptance(project, state):
            return "Base acceptance differs from the current attempt"
    return None


def _restart_pause(project: Path, change: dict, state: dict, reason: str,
                   *, rejected: bool = False) -> dict:
    target = restart_base_attempt(project, state, rejected=rejected)
    if target == "CONTRACT_COMMITTED":
        return {"status": target, "change_id": change["change_id"], "reason": reason,
                "next": f"storyloop run <project> --change {change['change_id']}"}
    return pause(project, state, target, reason=reason,
                 next=f"storyloop run <project> --change {change['change_id']} "
                      "to reverify the retained human candidate")


def record_base_review(project: Path, change_id: str, verdict: str, note: str) -> dict:
    """Bind a named independent human review to one committed candidate."""
    project = Path(project).resolve()
    with locked(project):
        return _record_base_review_locked(project, change_id, verdict, note)


def _record_base_review_locked(project: Path, change_id: str, verdict: str, note: str) -> dict:
    state = load_json(run_path(project, change_id))
    require(state.get("status") == "BASE_REVIEW_REQUIRED",
            "Base review needs a verified green candidate")
    require(verdict in {"pass", "fail"}, "Base review verdict must be pass or fail")
    require(_git(project, "rev-parse", "HEAD").strip() == state.get("candidate_commit"),
            "Base candidate changed before review")
    require(not _dirty_paths(project), "Base review needs a clean candidate worktree")
    evidence = trusted_base_evidence(project, state)
    require(evidence is not None and evidence.get("status") == "PASSED"
            and evidence.get("candidate_commit") == state["candidate_commit"]
            and evidence.get("candidate_sha256") == state.get("candidate_sha256"),
            "Base review needs a verified green candidate")
    reviewer = text(_git(project, "config", "user.name").strip(), "reviewer")
    require(reviewer not in base_candidate_authors(project, state),
            "Base reviewer must differ from every implementation author")
    record = {"version": 1, "change_id": change_id, "verdict": verdict,
              "note": text(note, "review note"), "reviewer": reviewer,
              "candidate_commit": state["candidate_commit"],
              "candidate_sha256": state["candidate_sha256"]}
    path = _review_path(project, change_id)
    write_json(path, record, max_bytes=1_000_000)
    state["base_review"] = record
    state["base_review_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    save_run(project, state)
    return {"status": "REVIEW_RECORDED", **record}


def _review_path(project: Path, change_id: str) -> Path:
    return project / ".storyloop/state/base-reviews" / f"{change_id}.json"


def _verify_candidate(project: Path, change: dict, state: dict) -> dict:
    base_candidate_paths(project, change, state)
    from .oracle_skill_approval import require_base_confirmation
    config = load_project_config(project)
    profile, _ = effective_base_profile(project, config["profile"], change["approval"])
    timeout = config["check_timeout_seconds"]
    require(base_commands_digest(profile) == change["approval"]["base_commands_sha256"],
            "Approved base commands changed before candidate verification")
    approval = require_base_confirmation(project, change, state)
    layers = {name: run_commands_isolated(project, layer_commands(profile, name), timeout)
              for name in ("unit", "integration", "checks")}
    for name, result in layers.items():
        require(not layer_commands(profile, name) or result["status"] == "PASSED",
                f"Base candidate {name} layer did not pass")
    smoke = run_system_smoke_isolated(project)
    require(smoke["status"] == "PASSED", "Base candidate system smoke did not pass")
    commands = profile["base_build_commands"]
    build = (run_commands_isolated(project, commands, timeout) if commands
             else {"status": "DECLARED_ABSENT", "results": []})
    require(build["status"] in {"PASSED", "DECLARED_ABSENT"},
            "Base candidate build did not pass")
    evidence = {"version": 1, "change_id": change["change_id"], "status": "PASSED",
                "candidate_commit": state["candidate_commit"],
                "candidate_sha256": implementation_digest(project),
                "engine_version": state["engine_version"],
                "engine_source": state["engine_source"],
                "target_tree_oid": _git(project, "rev-parse",
                                         f"{state['candidate_commit']}^{{tree}}").strip(),
                "contract_digest": state["contract_digest"], "layers": layers,
                "system_smoke": smoke, "build": build}
    if approval is not None:
        evidence["oracle_skills_approval"] = approval
    write_json(_evidence_path(project, change["change_id"]), evidence, max_bytes=4_000_000)
    return evidence


def _evidence_path(project: Path, change_id: str) -> Path:
    return project / ".storyloop/evidence" / change_id / "base-candidate-green.json"


def _candidate_to_review(project: Path, change: dict, state: dict) -> dict:
    candidate = _git(project, "rev-parse", "HEAD").strip()
    if candidate == state.get("rejected_candidate_commit"):
        return pause(project, state, "HUMAN_IMPLEMENTATION_REQUIRED",
                     reason="Rejected base candidate needs a new implementation commit",
                     next=f"Commit a revised candidate, then storyloop run <project> "
                          f"--change {change['change_id']}")
    state["candidate_commit"] = candidate
    state["base_inflight"] = "candidate_verification"
    save_run(project, state)
    try:
        base_candidate_paths(project, change, state)
        from .run_guards import guard_g1
        try:
            guard_g1(project, state, {})
        except StoryLoopError:
            _prove_old_green(project, change, state)
        evidence = _verify_candidate(project, change, state)
    except StoryLoopError as exc:
        return _restart_pause(project, change, state, str(exc))
    transition(project, state, "IMPLEMENTING", event="human_candidate_ready",
               updates={"candidate_commit": candidate, "base_evidence": evidence,
                        "candidate_sha256": evidence["candidate_sha256"],
                        "base_evidence_sha256": _evidence_sha256(project, change["change_id"])})
    state.pop("base_inflight", None)
    return _pause_review(project, change, state)


def _prove_old_green(project: Path, change: dict, state: dict) -> None:
    establish_old_green(project, change, state)
    path = project / ".storyloop/evidence" / change["change_id"] / "base-old-green.json"
    state["old_green_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    from .run_guards import guard_g1
    guard_g1(project, state, {})


def _evidence_sha256(project: Path, change_id: str) -> str:
    return hashlib.sha256(_evidence_path(project, change_id).read_bytes()).hexdigest()


def _pause_review(project: Path, change: dict, state: dict) -> dict:
    return pause(project, state, "BASE_REVIEW_REQUIRED",
                 next=f"storyloop base-review <project> --change {change['change_id']} "
                      "--verdict pass --note '<核对内容>', then storyloop run")


def _review_stop(project: Path, change: dict, state: dict) -> dict:
    review = state.get("base_review")
    if review is None:
        return _pause_review(project, change, state)
    if review.get("verdict") == "fail":
        return _restart_pause(project, change, state, "Human candidate needs a new attempt",
                              rejected=True)
    require(review.get("verdict") == "pass", "Independent base review is invalid")
    require(review.get("reviewer") not in base_candidate_authors(project, state),
            "Base reviewer must differ from implementation author")
    return pause(project, state, "MANUAL_ACCEPTANCE_REQUIRED",
                 candidate_sha256=state["candidate_sha256"],
                 pending=["BASE-ACCEPT"],
                 next=f"storyloop accept <project> --change {change['change_id']} "
                      "--criterion BASE-ACCEPT --note '<核对内容>', then storyloop run")


def _finish_after_acceptance(project: Path, change: dict, state: dict) -> dict:
    if not state.get("manual_acceptance_sha256"):
        return pause(project, state, "MANUAL_ACCEPTANCE_REQUIRED",
                     candidate_sha256=state["candidate_sha256"], pending=["BASE-ACCEPT"],
                     next=f"Re-record base acceptance for {change['change_id']}, then storyloop run")
    digest = state["candidate_sha256"]
    records = load_manual_acceptance(project, change["change_id"])
    require(any(row.get("criterion_id") == "BASE-ACCEPT" and row.get("candidate_sha256") == digest
                for row in records), "Base manual acceptance is missing")
    return _complete_base(project, change, state, records)


def _complete_base(project: Path, change: dict, state: dict, records: list[dict]) -> dict:
    evidence = state["base_evidence"]
    review = state["base_review"]
    from .oracle_skill_approval import base_confirmation_record
    state["base_inflight"] = "report_delivery"
    save_run(project, state)
    transition(project, state, "VERIFIED", event="verified", change=change,
               context={"base_evidence": evidence,
                        "oracle_skills_approval": base_confirmation_record(project, change, state)},
               effect=lambda: _verified_report(project, change, state, evidence, review, records))
    state.pop("base_inflight", None)
    save_run(project, state)
    return _finish_verified(project, change, state)


def _finish_verified(project: Path, change: dict, state: dict) -> dict:
    if current_branch(project) == state["source_branch"]:
        transition(project, state, "MERGED", event="recover_merge")
    else:
        transition(project, state, "MERGED", event="merge", effect=lambda: {
            "merge_commit": finish_feature(project, state["feature_branch"],
                                            state["source_branch"])["merge_commit"]})
    save_run(project, state)
    return _finish_merged(project, change, state)


def _finish_merged(project: Path, change: dict, state: dict) -> dict:
    require(current_branch(project) == state["source_branch"]
            and _git(project, "rev-parse", "HEAD").strip() == state["merge_commit"],
            "Merged base branch changed before receipting")
    transition(project, state, "RECEIPTING", event="receipting")
    save_run(project, state)
    return _base_receipt(project, change, state)


def _verified_report(project: Path, change: dict, state: dict, evidence: dict,
                     review: dict, records: list[dict]) -> dict:
    report = {"version": 1, "status": "PASSED", "change_id": change["change_id"],
              "nature": "base", "scope": change["product"]["base"]["scope"],
              "goal": change["product"]["goal"], "paths": change["product"]["base"]["paths"],
              "implementation_commit": state["candidate_commit"],
              "baseline_version_before": change["approval"]["versions"]["baseline_version"],
              "verification": evidence, "review": review,
              "manual_acceptance": records,
              "build_note": "项目声明无构建步骤" if evidence["build"]["status"] == "DECLARED_ABSENT" else None}
    if evidence.get("oracle_skills_approval") is not None:
        report["oracle_skills_approval"] = evidence["oracle_skills_approval"]
    path = project / "reports/storyloop" / change["change_id"] / "report.json"
    write_json(path, report, max_bytes=8_000_000)
    write_text_atomic(path.with_suffix(".md"), _base_markdown(report))
    paths = [path.relative_to(project).as_posix(),
             path.with_suffix(".md").relative_to(project).as_posix()]
    commit = commit_paths(project, paths,
                          f"docs({change['change_id']}): record base verification")["commit"]
    return {"implementation_commit": state["candidate_commit"], "report_commit": commit,
            "report": str(path), "report_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "acceptance_evidence_sha256": hashlib.sha256(_evidence_path(project, change["change_id"]).read_bytes()).hexdigest(),
            "review": review}


def _base_markdown(report: dict) -> str:
    lines = [f"# {report['change_id']} base 验证报告", "", f"目标：{report['goal']}", "",
             "## 批准路径", "", *(f"- `{path}`" for path in report["paths"]), "",
             "## 审查与人工验收", "", f"审查者：{report['review']['reviewer']}",
             f"结论：{report['review']['verdict']}",
             f"人工验收：{report['manual_acceptance'][0]['note']}"]
    if report.get("build_note"):
        lines += ["", report["build_note"]]
    if report.get("oracle_skills_approval"):
        paths = report["oracle_skills_approval"]["paths"]
        lines += ["", "Oracle 技能确认：" + "、".join(f"`{path}`" for path in paths)]
    if report.get("merge_commit"):
        lines += ["", f"合并提交：`{report['merge_commit']}`"]
    return "\n".join(lines) + "\n"


def _base_receipt(project: Path, change: dict, state: dict) -> dict:
    from .project_receipt import authoritative_report, ensure_merge_receipt
    report = authoritative_report(project, state)
    report["merge_commit"] = state["merge_commit"]
    receipt_bytes = ((json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
                     _base_markdown(report).encode("utf-8"))
    receipt_commit = ensure_merge_receipt(project, state, receipt_bytes)
    transition(project, state, "FINALIZING", event="finalizing",
               updates={"integration_commit": receipt_commit})
    save_run(project, state)
    return {"status": "FINALIZING", "change_id": change["change_id"],
            "integration_commit": receipt_commit}


def _finalize_base(project: Path, change: dict, state: dict) -> dict:
    from .base_finalization import finalize_base_baseline
    from .project_loop_finish import passed_result
    from .run_transitions import set_change_status
    try:
        finalize_base_baseline(project, change, state)
    except StoryLoopError as exc:
        if exc.code != "BASELINE_MISMATCH":
            raise
        return pause(project, state, "WORKSPACE_TAINTED",
                     base_resume_status="FINALIZING", baseline_issue=str(exc),
                     next="Restore the approved .storyloop/baseline.json snapshot; "
                          "then storyloop run <project> --change " + change["change_id"])
    if change["status"] != "PASSED":
        set_change_status(project, change, "PASSED")
    transition(project, state, "PASSED", event="passed")
    save_run(project, state)
    return passed_result(state)


def _prepare(project: Path, change: dict, state: dict) -> dict:
    change_id = change["change_id"]
    contract_root = materialize_approved_change(project, change_id)
    paths = relative_files(project, [contract_root])
    if state["status"] == "PREPARING":
        transition(project, state, "CONTRACT_READY", event="contract_ready")
        save_run(project, state)
    if state["status"] == "CONTRACT_READY":
        commit = _ensure_base_contract_commit(project, state, paths)
        transition(project, state, "CONTRACT_COMMITTED", event="contract_committed",
                   updates={"contract_commit": commit})
        save_run(project, state)
    state["base_inflight"] = "old_green"
    save_run(project, state)
    _prove_old_green(project, change, state)
    transition(project, state, "IMPLEMENTING", event="base_green_ready")
    state.pop("base_inflight", None)
    save_run(project, state)
    if state.get("implementer", "human") == "agent":
        return {"status": "IMPLEMENTING", "change_id": change_id,
                "next": f"storyloop run <project> --change {change_id}"}
    return pause(project, state, "HUMAN_IMPLEMENTATION_REQUIRED",
                 next=f"Commit the approved paths on {state['feature_branch']}, then "
                      f"storyloop run <project> --change {change_id}")


def _ensure_base_contract_commit(project: Path, state: dict, paths: list[str]) -> str:
    """Recognize a completed contract commit even when approved base files remain dirty."""
    head = _git(project, "rev-parse", "HEAD").strip()
    if head == state["source_head"]:
        return commit_paths(project, paths,
                            f"test({state['change_id']}): lock approved contract and smoke acceptance")["commit"]
    require(exact_child_commit(project, state["source_head"], set(paths), head),
            "Cannot recover the base contract commit safely")
    return head
