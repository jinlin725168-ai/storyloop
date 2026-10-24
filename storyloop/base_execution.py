"""Human base execution through the shared run state and transition guards."""

from __future__ import annotations

from pathlib import Path
import hashlib

from .approvals import materialize_approved_change
from .base_upgrade import base_candidate_paths, establish_old_green
from .baseline import _git, load_project_config, verify_baseline
from .manual_acceptance import load_manual_acceptance
from .models import require, text
from .project_git import current_branch, start_base, commit_paths, finish_feature
from .project_loop_support import relative_files
from .project_preparation import _ensure_contract_commit
from .project_profile import base_commands_digest, layer_commands
from .run_state import run_path, save_run
from .run_stops import pause
from .run_transitions import transition
from .smoke_runner import implementation_digest, run_commands_isolated
from .storage import load_json, write_json, write_text_atomic
from .system_smoke import run_system_smoke_isolated


def run_base_change(project: Path, change: dict, implementer: str | None, new_run_state) -> dict:
    """Open or resume the manually implemented base change without an agent call."""
    state = _open_state(project, change, implementer, new_run_state)
    if state["status"] in {"PREPARING", "CONTRACT_READY", "CONTRACT_COMMITTED"}:
        return _prepare(project, change, state)
    if state["status"] == "HUMAN_IMPLEMENTATION_REQUIRED":
        base_candidate_paths(project, change, state)
        candidate = _git(project, "rev-parse", "HEAD").strip()
        transition(project, state, "IMPLEMENTING", event="human_candidate_ready",
                   updates={"candidate_commit": candidate})
        save_run(project, state)
        return {"status": "IMPLEMENTING", "change_id": change["change_id"]}
    if state["status"] in {"IMPLEMENTING", "BASE_REVIEW_REQUIRED"}:
        return _verify_and_pause(project, change, state)
    if state["status"] == "MANUAL_ACCEPTANCE_REQUIRED":
        return _finish_after_acceptance(project, change, state)
    return {"status": state["status"], "change_id": change["change_id"]}


def record_base_review(project: Path, change_id: str, verdict: str, note: str) -> dict:
    """Bind a named independent human review to one committed candidate."""
    project = Path(project).resolve()
    state = load_json(run_path(project, change_id))
    require(state.get("status") in {"IMPLEMENTING", "BASE_REVIEW_REQUIRED"},
            "Base review needs a committed candidate")
    require(verdict in {"pass", "fail"}, "Base review verdict must be pass or fail")
    require(_git(project, "rev-parse", "HEAD").strip() == state.get("candidate_commit"),
            "Base candidate changed before review")
    reviewer = text(_git(project, "config", "user.name").strip(), "reviewer")
    author = _git(project, "show", "-s", "--format=%an", "HEAD").strip()
    require(reviewer != author, "Base reviewer must differ from the implementation author")
    record = {"version": 1, "change_id": change_id, "verdict": verdict,
              "note": text(note, "review note"), "reviewer": reviewer,
              "candidate_commit": state["candidate_commit"],
              "candidate_sha256": implementation_digest(project)}
    write_json(_review_path(project, change_id), record, max_bytes=1_000_000)
    return {"status": "REVIEW_RECORDED", **record}


def _review_path(project: Path, change_id: str) -> Path:
    return project / ".storyloop/state/base-reviews" / f"{change_id}.json"


def _verify_candidate(project: Path, change: dict, state: dict) -> dict:
    base_candidate_paths(project, change, state)
    require(_git(project, "rev-parse", "HEAD").strip() == state["candidate_commit"],
            "Base candidate changed after review")
    config = load_project_config(project)
    profile, timeout = config["profile"], config["check_timeout_seconds"]
    require(base_commands_digest(profile) == change["approval"]["base_commands_sha256"],
            "Approved base commands changed before candidate verification")
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
                "contract_digest": state["contract_digest"], "layers": layers,
                "system_smoke": smoke, "build": build}
    write_json(_evidence_path(project, change["change_id"]), evidence, max_bytes=4_000_000)
    return evidence


def _evidence_path(project: Path, change_id: str) -> Path:
    return project / ".storyloop/evidence" / change_id / "base-candidate-green.json"


def _verify_and_pause(project: Path, change: dict, state: dict) -> dict:
    evidence = _verify_candidate(project, change, state)
    path = _review_path(project, change["change_id"])
    if not path.is_file():
        return pause(project, state, "BASE_REVIEW_REQUIRED",
                     next=f"storyloop base-review <project> --change {change['change_id']} "
                          "--verdict pass --note '<核对内容>', then storyloop run")
    review = load_json(path)
    require(review.get("verdict") == "pass" and review.get("candidate_commit") == state["candidate_commit"]
            and review.get("candidate_sha256") == evidence["candidate_sha256"],
            "Independent base review did not pass for this candidate")
    require(review.get("reviewer") != _git(project, "show", "-s", "--format=%an",
                                           state["candidate_commit"]).strip(),
            "Base reviewer must differ from implementation author")
    state["base_review"] = review
    state["base_evidence"] = evidence
    save_run(project, state)
    return pause(project, state, "MANUAL_ACCEPTANCE_REQUIRED",
                 candidate_sha256=evidence["candidate_sha256"],
                 pending=["BASE-ACCEPT"],
                 next=f"storyloop accept <project> --change {change['change_id']} "
                      "--criterion BASE-ACCEPT --note '<核对内容>', then storyloop run")


def _finish_after_acceptance(project: Path, change: dict, state: dict) -> dict:
    digest = implementation_digest(project)
    require(digest == state.get("candidate_sha256"), "Base candidate changed during manual acceptance")
    records = load_manual_acceptance(project, change["change_id"])
    require(any(row.get("criterion_id") == "BASE-ACCEPT" and row.get("candidate_sha256") == digest
                for row in records), "Base manual acceptance is missing")
    return _complete_base(project, change, state, records)


def _complete_base(project: Path, change: dict, state: dict, records: list[dict]) -> dict:
    evidence_path = _evidence_path(project, change["change_id"])
    evidence = load_json(evidence_path)
    review = load_json(_review_path(project, change["change_id"]))
    transition(project, state, "VERIFIED", event="verified", change=change,
               context={"base_evidence": evidence},
               effect=lambda: _verified_report(project, change, state, evidence, review, records))
    save_run(project, state)
    transition(project, state, "MERGED", event="merge", effect=lambda: {
        "merge_commit": finish_feature(project, state["feature_branch"],
                                        state["source_branch"])["merge_commit"]})
    save_run(project, state)
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
    path = project / "reports/storyloop" / change["change_id"] / "report.json"
    write_json(path, report, max_bytes=8_000_000)
    write_text_atomic(path.with_suffix(".md"), _base_markdown(report))
    commit = commit_paths(project, [path.relative_to(project).as_posix(),
                                    path.with_suffix(".md").relative_to(project).as_posix()],
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
    if report.get("merge_commit"):
        lines += ["", f"合并提交：`{report['merge_commit']}`"]
    return "\n".join(lines) + "\n"


def _base_receipt(project: Path, change: dict, state: dict) -> dict:
    path = Path(state["report"])
    report = load_json(path)
    report["merge_commit"] = state["merge_commit"]
    write_json(path, report, max_bytes=8_000_000)
    write_text_atomic(path.with_suffix(".md"), _base_markdown(report))
    receipt_commit = commit_paths(project, [path.relative_to(project).as_posix(),
                                            path.with_suffix(".md").relative_to(project).as_posix()],
                                  f"docs({change['change_id']}): record base merge")["commit"]
    transition(project, state, "FINALIZING", event="finalizing",
               updates={"integration_commit": receipt_commit})
    save_run(project, state)
    return {"status": "FINALIZING", "change_id": change["change_id"],
            "integration_commit": receipt_commit}


def _open_state(project: Path, change: dict, implementer: str | None, new_run_state) -> dict:
    change_id = change["change_id"]
    saved = run_path(project, change_id)
    require(change["status"] == "APPROVED" and change["approval"],
            "Base change must be approved before execution")
    if saved.exists():
        state = load_json(saved)
        require(state.get("contract_digest") == change["approval"]["contract_digest"]
                and state.get("implementer") == "human", "Saved base run is invalid")
        require(implementer in {None, "human"}, "Base agent implementation belongs to E2")
        require(current_branch(project) == state["feature_branch"],
                "Resume the saved StoryLoop base branch")
        return state
    require(implementer == "human", "Use --implementer human for E1 base changes")
    verify_baseline(project)
    state = new_run_state(project, change_id, start_base(project, change_id),
                          change["approval"]["contract_digest"])
    state["implementer"] = "human"
    save_run(project, state)
    return state


def _prepare(project: Path, change: dict, state: dict) -> dict:
    change_id = change["change_id"]
    contract_root = materialize_approved_change(project, change_id)
    paths = relative_files(project, [contract_root])
    if state["status"] == "PREPARING":
        transition(project, state, "CONTRACT_READY", event="contract_ready")
        save_run(project, state)
    if state["status"] == "CONTRACT_READY":
        commit = _ensure_contract_commit(project, state, paths)
        transition(project, state, "CONTRACT_COMMITTED", event="contract_committed",
                   updates={"contract_commit": commit})
        save_run(project, state)
    establish_old_green(project, change, state)
    transition(project, state, "IMPLEMENTING", event="base_green_ready")
    save_run(project, state)
    return pause(project, state, "HUMAN_IMPLEMENTATION_REQUIRED",
                 next=f"Commit the approved paths on {state['feature_branch']}, then "
                      f"storyloop run <project> --change {change_id}")
