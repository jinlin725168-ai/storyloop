"""Human base execution through the shared run state and transition guards."""

from __future__ import annotations

from pathlib import Path
import hashlib
import json

from . import __version__
from .approvals import materialize_approved_change
from .base_upgrade import _changed_since, base_candidate_paths, establish_old_green
from .baseline import _git, load_baseline, load_project_config, verify_baseline
from .manual_acceptance import load_manual_acceptance
from .models import StoryLoopError, require, text
from .project_git import changed_paths, current_branch, start_base, commit_paths, exact_child_commit, finish_feature
from .project_loop_support import relative_files
from .project_profile import base_commands_digest, layer_commands
from .project_boundaries import audit_base_scope
from .run_state import run_path, save_run
from .run_stops import pause
from .run_transitions import transition
from .run_guards import (base_candidate_authors, base_report_commit, base_report_paths,
                         saved_base_evidence, trusted_base_acceptance, trusted_base_evidence,
                         trusted_base_review)
from .project_receipt import _dirty_paths
from .smoke_runner import implementation_digest, run_commands_isolated
from .storage import load_json, write_json, write_text_atomic
from .system_smoke import run_system_smoke_isolated


def run_base_change(project: Path, change: dict, implementer: str | None, new_run_state) -> dict:
    """Open or resume the manually implemented base change without an agent call."""
    state = _open_state(project, change, implementer, new_run_state)
    if state["status"] in {"PREPARING", "CONTRACT_READY", "CONTRACT_COMMITTED"}:
        return _prepare(project, change, state)
    if state["status"] == "HUMAN_IMPLEMENTATION_REQUIRED":
        return _candidate_to_review(project, change, state)
    if state["status"] == "IMPLEMENTING":
        if _git(project, "rev-parse", "HEAD").strip() != state.get("candidate_commit"):
            return _reopen_human(project, change, state)
        return _candidate_to_review(project, change, state)
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


def record_base_review(project: Path, change_id: str, verdict: str, note: str) -> dict:
    """Bind a named independent human review to one committed candidate."""
    project = Path(project).resolve()
    state = load_json(run_path(project, change_id))
    require(state.get("status") == "BASE_REVIEW_REQUIRED",
            "Base review needs a verified green candidate")
    require(verdict in {"pass", "fail"}, "Base review verdict must be pass or fail")
    require(_git(project, "rev-parse", "HEAD").strip() == state.get("candidate_commit")
            or base_report_commit(project, state),
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
    report_child = _candidate_scope(project, change, state)
    from .oracle_skill_approval import require_base_confirmation
    config = load_project_config(project)
    profile, timeout = config["profile"], config["check_timeout_seconds"]
    require(base_commands_digest(profile) == change["approval"]["base_commands_sha256"],
            "Approved base commands changed before candidate verification")
    cached = trusted_base_evidence(project, state)
    approval = require_base_confirmation(project, change, state,
                                         cached or saved_base_evidence(state))
    if cached is not None:
        if (cached.get("status") == "PASSED"
                and cached.get("candidate_commit") == state["candidate_commit"]
                and cached.get("candidate_sha256") == state.get("candidate_sha256")
                and cached.get("contract_digest") == state["contract_digest"]
                and cached.get("engine_version") == state["engine_version"]
                and cached.get("engine_source") == state["engine_source"]
                and cached.get("target_tree_oid")
                == _git(project, "rev-parse", f"{state['candidate_commit']}^{{tree}}").strip()
                and cached.get("oracle_skills_approval") == approval):
            return cached
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
                "candidate_sha256": (state["candidate_sha256"] if report_child
                                      else implementation_digest(project)),
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


def _candidate_scope(project: Path, change: dict, state: dict) -> str | None:
    report_child = base_report_commit(project, state)
    if report_child:
        actual = changed_paths(project, state["contract_commit"], state["candidate_commit"])
        require(actual == set(change["product"]["base"]["paths"]),
                "Base candidate paths changed after report commit")
        audit_base_scope(project, change, actual)
    else:
        base_candidate_paths(project, change, state)
    require(_git(project, "rev-parse", "HEAD").strip() == state["candidate_commit"] or report_child,
            "Base candidate changed after review")
    return report_child


def _evidence_path(project: Path, change_id: str) -> Path:
    return project / ".storyloop/evidence" / change_id / "base-candidate-green.json"


def _candidate_to_review(project: Path, change: dict, state: dict) -> dict:
    base_candidate_paths(project, change, state)
    candidate = _git(project, "rev-parse", "HEAD").strip()
    require(candidate != state.get("rejected_candidate_commit"),
            "Rejected base candidate needs a new implementation commit")
    state["candidate_commit"] = candidate
    try:
        evidence = _verify_candidate(project, change, state)
    except Exception:
        if state["status"] == "HUMAN_IMPLEMENTATION_REQUIRED":
            state.pop("candidate_commit", None)
        raise
    if state["status"] == "HUMAN_IMPLEMENTATION_REQUIRED":
        transition(project, state, "IMPLEMENTING", event="human_candidate_ready",
                   updates={"candidate_commit": candidate, "base_evidence": evidence,
                            "candidate_sha256": evidence["candidate_sha256"],
                            "base_evidence_sha256": _evidence_sha256(project, change["change_id"])})
    else:
        state["base_evidence"] = evidence
        state["candidate_sha256"] = evidence["candidate_sha256"]
        state["base_evidence_sha256"] = _evidence_sha256(project, change["change_id"])
    return _pause_review(project, change, state)


def _evidence_sha256(project: Path, change_id: str) -> str:
    return hashlib.sha256(_evidence_path(project, change_id).read_bytes()).hexdigest()


def _pause_review(project: Path, change: dict, state: dict) -> dict:
    return pause(project, state, "BASE_REVIEW_REQUIRED",
                 next=f"storyloop base-review <project> --change {change['change_id']} "
                      "--verdict pass --note '<核对内容>', then storyloop run")


def _reopen_human(project: Path, change: dict, state: dict, *, rejected: bool = False) -> dict:
    old_candidate = state.get("candidate_commit")
    transition(project, state, "HUMAN_IMPLEMENTATION_REQUIRED", event="base_revise")
    for key in ("candidate_commit", "candidate_sha256", "base_review", "base_review_sha256",
                "base_evidence", "base_evidence_sha256"):
        state.pop(key, None)
    if rejected:
        state["rejected_candidate_commit"] = old_candidate
    save_run(project, state)
    return pause(project, state, "HUMAN_IMPLEMENTATION_REQUIRED",
                 next=f"Revise the approved paths and commit on {state['feature_branch']}, "
                      f"then storyloop run <project> --change {change['change_id']}")


def _review_stop(project: Path, change: dict, state: dict) -> dict:
    if (_git(project, "rev-parse", "HEAD").strip() != state.get("candidate_commit")
            and not base_report_commit(project, state)):
        return _reopen_human(project, change, state)
    if trusted_base_evidence(project, state) is None:
        _refresh_base_evidence(project, change, state)
    review = trusted_base_review(project, state)
    if review is None:
        return _pause_review(project, change, state)
    evidence = trusted_base_evidence(project, state)
    if (review.get("candidate_commit") != state["candidate_commit"]
            or review.get("candidate_sha256") != evidence["candidate_sha256"]):
        return _pause_review(project, change, state)
    if review.get("verdict") == "fail":
        return _reopen_human(project, change, state, rejected=True)
    require(review.get("verdict") == "pass", "Independent base review is invalid")
    require(review.get("reviewer") not in base_candidate_authors(project, state),
            "Base reviewer must differ from implementation author")
    state["base_review"] = review
    state["base_evidence"] = evidence
    save_run(project, state)
    return pause(project, state, "MANUAL_ACCEPTANCE_REQUIRED",
                 candidate_sha256=evidence["candidate_sha256"],
                 pending=["BASE-ACCEPT"],
                 next=f"storyloop accept <project> --change {change['change_id']} "
                      "--criterion BASE-ACCEPT --note '<核对内容>', then storyloop run")


def _refresh_base_evidence(project: Path, change: dict, state: dict) -> dict:
    saved = saved_base_evidence(state) if base_report_commit(project, state) else None
    evidence = _verify_candidate(project, change, state)
    if saved is not None:
        write_json(_evidence_path(project, change["change_id"]), saved, max_bytes=4_000_000)
        evidence = saved
    state["base_evidence"] = evidence
    state["base_evidence_sha256"] = _evidence_sha256(project, change["change_id"])
    save_run(project, state)
    return evidence


def _finish_after_acceptance(project: Path, change: dict, state: dict) -> dict:
    head = _git(project, "rev-parse", "HEAD").strip()
    report_commit = base_report_commit(project, state)
    if head != state.get("candidate_commit") and not report_commit:
        return _reopen_human(project, change, state)
    allowed_dirty = base_report_paths(state) if head == state["candidate_commit"] else set()
    require(_dirty_paths(project) <= allowed_dirty,
            "Base candidate worktree changed during report recovery")
    if trusted_base_evidence(project, state) is None:
        _refresh_base_evidence(project, change, state)
    if trusted_base_review(project, state) is None:
        transition(project, state, "BASE_REVIEW_REQUIRED", event="base_review_recheck")
        state.pop("base_review", None)
        state.pop("base_review_sha256", None)
        save_run(project, state)
        return _pause_review(project, change, state)
    if not trusted_base_acceptance(project, state):
        return pause(project, state, "MANUAL_ACCEPTANCE_REQUIRED",
                     candidate_sha256=state["candidate_sha256"], pending=["BASE-ACCEPT"],
                     next=f"Re-record base acceptance for {change['change_id']}, then storyloop run")
    digest = state["candidate_sha256"]
    records = load_manual_acceptance(project, change["change_id"])
    require(any(row.get("criterion_id") == "BASE-ACCEPT" and row.get("candidate_sha256") == digest
                for row in records), "Base manual acceptance is missing")
    return _complete_base(project, change, state, records)


def _complete_base(project: Path, change: dict, state: dict, records: list[dict]) -> dict:
    evidence_path = _evidence_path(project, change["change_id"])
    evidence = load_json(evidence_path)
    review = load_json(_review_path(project, change["change_id"]))
    from .oracle_skill_approval import base_confirmation_record
    transition(project, state, "VERIFIED", event="verified", change=change,
               context={"base_evidence": evidence,
                        "oracle_skills_approval": base_confirmation_record(project, change, state)},
               effect=lambda: _verified_report(project, change, state, evidence, review, records))
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
    commit = base_report_commit(project, state)
    if commit:
        expected = [path.read_bytes(), path.with_suffix(".md").read_bytes()]
        actual = [_git(project, "show", f"{commit}:{relative}").encode("utf-8")
                  for relative in paths]
        if actual != expected:
            _git(project, "update-index", "--no-skip-worktree", "--", *paths)
            commit = commit_paths(project, paths,
                                  f"docs({change['change_id']}): correct base verification")['commit']
    else:
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


def _open_state(project: Path, change: dict, implementer: str | None, new_run_state) -> dict:
    change_id = change["change_id"]
    saved = run_path(project, change_id)
    require(change["status"] in {"APPROVED", "PASSED"} and change["approval"],
            "Base change must be approved before execution")
    if saved.exists():
        state = load_json(saved)
        require(state.get("contract_digest") == change["approval"]["contract_digest"]
                and state.get("implementer") == "human", "Saved base run is invalid")
        require(implementer in {None, "human"}, "Base agent implementation belongs to E2")
        expected = (state["source_branch"] if state["status"] in
                    {"MERGED", "RECEIPTING", "FINALIZING", "PASSED"}
                    or (state["status"] == "WORKSPACE_TAINTED"
                        and state.get("base_resume_status") == "FINALIZING")
                    else state["feature_branch"])
        require(current_branch(project) == expected
                or (state["status"] == "VERIFIED" and current_branch(project) == state["source_branch"]),
                "Resume the saved StoryLoop base branch")
        return state
    require(implementer == "human", "Use --implementer human for E1 base changes")
    require(change["approval"]["versions"]["engine_version"] == __version__,
            "Approved base engine version differs from the running engine")
    dirty = _git(project, "status", "--porcelain=v1", "--untracked-files=all", "-z")
    records = [item for item in dirty.split("\0") if item]
    expected = set(change["product"]["base"]["paths"])
    allowed = {item[3:].replace("\\", "/") for item in records}
    require(all(item[:2] in {" M", " D", "??"} for item in records)
            and allowed <= expected, "Prepared base files exceed approved paths")
    audit_base_scope(project, change, expected)
    baseline = load_baseline(project)
    committed = _changed_since(project, baseline["source_head"])
    require(not committed.intersection(expected),
            "Committed proposal paths already exist on the source branch; "
            "revert them with a new commit, then replay the change on the new base branch")
    start = _base_start_context(project, change_id, baseline, allowed)
    state = new_run_state(project, change_id, start,
                          change["approval"]["contract_digest"])
    state["implementer"] = "human"
    state["engine_version"] = __version__
    state["engine_source"] = str(Path(__file__).resolve().parent)
    state["target_tree_oid"] = _git(project, "rev-parse", f"{state['source_head']}^{{tree}}").strip()
    save_run(project, state)
    return state


def _base_start_context(project: Path, change_id: str, baseline: dict,
                        allowed: set[str]) -> dict:
    branch = f"storyloop/{change_id}"
    if current_branch(project) == branch:
        head = _git(project, "rev-parse", "HEAD").strip()
        require(head == _git(project, "rev-parse", baseline["source_branch"]).strip(),
                "Unrecorded base branch no longer matches the locked source")
        verify_baseline(project, resume=True, allowed_base_paths=allowed)
        return {"status": "STARTED", "branch": branch,
                "source_branch": baseline["source_branch"], "source_head": head}
    verify_baseline(project, allowed_base_paths=allowed)
    return start_base(project, change_id, allowed)


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
    establish_old_green(project, change, state)
    old_green = project / ".storyloop/evidence" / change_id / "base-old-green.json"
    state["old_green_sha256"] = hashlib.sha256(old_green.read_bytes()).hexdigest()
    transition(project, state, "IMPLEMENTING", event="base_green_ready")
    save_run(project, state)
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
