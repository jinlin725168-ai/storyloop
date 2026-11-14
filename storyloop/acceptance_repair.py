"""`storyloop acceptance-repair`: the only channel for fixing defects in a change's acceptance suite.

The implementer never touches the suite. A human requests ordinary repairs; bounded fixture
errors in RED may be repaired automatically by the acceptance role. Every replacement suite
re-proves RED at the contract commit, voiding earlier attempts.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import shlex
from typing import TYPE_CHECKING

from .baseline import load_project_config
from .changes import load_change
from .models import StoryLoopError, identifier, require, text
from .oracle_counts import ensure_baseline
from .project_git import commit_paths, current_branch
from .guarded_calls import read_only_call
from .project_loop_support import relative_files, run_path, save_run
from .run_stops import pause
from .run_transitions import transition, REPAIRABLE
from .smoke import RED_EVIDENCE, RESULT_EVIDENCE, install_smoke_suite, without_manifest_echo
from .smoke import suite_relative, suite_root as acceptance_suite_root
from .smoke_runner import run_commands_at_commit, run_smoke_isolated
from .storage import load_json
from .verification_entries import check_entry_use
from .acceptance_progress import (DEFAULT_AUTO_RETRY_ROUND_LIMIT, PROGRESS_CATEGORIES,
                                  remember_round)

if TYPE_CHECKING:  # the loop imports this module; the services type is only an annotation
    from .run_services import LoopServices

AUTO_REASON = "RED 证明中夹具/前提失败"
DEFAULT_FIXTURE_RETRY_LIMIT = 2





def repair_smoke(project: Path, change_id: str, reason: str, services: LoopServices | None = None,
                 now=None) -> dict:
    """Let the smoke owner fix test defects in its own suite, then re-prove RED at the contract commit.

    The implementer never touches the suite. The human reason is recorded, the old attempts are
    voided, and the new suite must still fail on the tree before implementation.
    """
    result = _repair_once(project, change_id, reason, services, now=now)
    return retry_fixture_red(project, change_id, services, result)


def retry_fixture_red(project: Path, change_id: str, services: LoopServices | None,
                      result: dict) -> dict:
    """Retry fixture defects by budget and deterministic defects while evidence improves."""
    project = Path(project).resolve()
    config = load_project_config(project)
    fixture_limit = config.get("acceptance_fixture_retry_limit", DEFAULT_FIXTURE_RETRY_LIMIT)
    round_limit = config.get("acceptance_auto_retry_round_limit", DEFAULT_AUTO_RETRY_ROUND_LIMIT)
    require(type(fixture_limit) is int and 0 <= fixture_limit <= 5,
            "Invalid acceptance_fixture_retry_limit")
    require(type(round_limit) is int and 0 <= round_limit <= 50,
            "Invalid acceptance_auto_retry_round_limit")
    state = load_json(run_path(project, change_id))
    while result.get("status") == "ORACLE_DEFECT":
        acceptance = state.get("acceptance") or {}
        if acceptance.get("fixture_repairable") is not True:
            break
        category = acceptance.get("defect_category")
        progressive = category in PROGRESS_CATEGORIES
        progress = state.get("acceptance_retry") or {}
        repairs = state.get("repairs") or []
        if (progress.get("exhausted") and repairs and repairs[-1].get("automatic") is not True
                and len(repairs) > (progress.get("rounds") or [{}])[-1].get("repair_count", -1)):
            progress.pop("exhausted", None)  # a human submitted a new suite after the stop
        if state.get("acceptance_retry", {}).get("exhausted"):
            break
        reason, finding_ids = remember_round(state, acceptance) if progressive else (None, [])
        automatic = [item for item in state.get("repairs") or [] if item.get("automatic") is True]
        used = len(automatic)
        fixture_used = sum(item.get("defect_category") not in PROGRESS_CATEGORIES for item in automatic)
        if progressive and reason == "no_progress":
            return _retry_stop(project, state, acceptance, reason, finding_ids, round_limit)
        if used >= round_limit:
            return _retry_stop(project, state, acceptance, "round_limit", finding_ids, round_limit)
        if not progressive and fixture_used >= fixture_limit:
            save_run(project, state)
            break
        save_run(project, state)
        result = _repair_once(project, change_id, AUTO_REASON, services, automatic=True)
        state = load_json(run_path(project, change_id))
    return result


def _retry_stop(project: Path, state: dict, acceptance: dict, reason: str,
                finding_ids: list[str], round_limit: int) -> dict:
    detail = {"reason": reason, "finding_ids": finding_ids, "round_limit": round_limit}
    state.setdefault("acceptance_retry", {"version": 1, "rounds": []}).update(
        exhausted=True, stop=detail, round_limit=round_limit)
    acceptance = {**acceptance, "retry_stop": detail}
    message = ("验收意见连续两轮无进展" if reason == "no_progress" else
               f"验收自动修复达到总轮次上限 {round_limit}")
    return pause(project, state, "ORACLE_DEFECT", error=message, acceptance=acceptance)


def _repair_once(project: Path, change_id: str, reason: str, services: LoopServices | None = None,
                 now=None, *, automatic: bool = False) -> dict:
    project = Path(project).resolve()
    change_id = identifier(change_id, "change id")
    reason = text(reason, "repair reason")
    change, state = _repair_state(project, change_id)
    from .review_convergence import repair_allowed
    if not automatic:
        require(repair_allowed(state), "完整判据规格后只允许一次定向修复；先完成整体重审")
    if services is None:
        from .default_loop_services import _default_services
        services = _default_services()
    suite_root = acceptance_suite_root(project, change_id)
    existing = [{"path": p.relative_to(suite_root).as_posix(), "content": p.read_text(encoding="utf-8")}
                for p in sorted(suite_root.rglob("*")) if p.is_file() and p.name != "manifest.json"]
    evidence = _last_smoke_evidence(project, change_id, state)
    request = {**change, "repair": {"reason": reason, "existing_files": existing,
                                    "manifest": load_json(suite_root / "manifest.json"),
                                    "failure_evidence": evidence}}
    from .design_approval import repair_reuse_snapshot
    reuse = repair_reuse_snapshot(project, change, state)
    generated = read_only_call(project, change_id, lambda: services.generate_smoke(project, request))
    require(isinstance(generated, dict) and {"files", "manifest"} <= set(generated) <= {"files", "manifest", "lessons_used", "errata"},
            "Smoke agent returned an invalid contract")
    files = without_manifest_echo(generated["files"])
    previous = _tracked_under(project, suite_relative(project, change_id))  # replaced files are committed as deletions
    install_smoke_suite(project, change, files, generated["manifest"], replace=True)
    for name in (RED_EVIDENCE, RESULT_EVIDENCE):
        (project / ".storyloop/evidence" / change_id / name).unlink(missing_ok=True)
    paths = sorted(set(relative_files(project, [suite_root])) | set(previous))
    commit = _commit_repair_paths(project, paths, f"test({change_id}): repair acceptance suite")
    moment = (now or (lambda: datetime.now(timezone.utc)))().astimezone(timezone.utc).isoformat(timespec="seconds")
    entry = {"at": moment, "reason": reason, "commit": commit["commit"],
             "voided_attempts": int(state.get("attempt", 0))}
    if automatic:
        entry["automatic"] = True
        category = (state.get("acceptance") or {}).get("defect_category")
        if category:
            entry["defect_category"] = category
    state.setdefault("repairs", []).append(entry)
    if not automatic:
        from .review_convergence import record_repair
        record_repair(state)
    from .intervention_events import append_event
    append_event(state, "acceptance_repair", source="engine" if automatic else "acceptance-repair",
                 stop=state.get("status"), at=moment, automatic=automatic,
                 defect_category=entry.get("defect_category"))
    try:
        return _prove_repaired_suite(project, change, state, commit, reuse, services)
    except Exception:
        from .consistency import invalidate_consistency
        invalidate_consistency(state, replan=True)
        state.pop("red_evidence_sha256", None)
        save_run(project, state)
        raise


def _repair_state(project: Path, change_id: str) -> tuple[dict, dict]:
    change = load_change(project, change_id)
    path = run_path(project, change_id)
    require(path.exists(), "Repair needs a started run with a committed contract")
    state = load_json(path)
    require(state.get("status") in REPAIRABLE and state.get("contract_commit"),
            f"Acceptance repair needs a committed contract that has not completed "
            f"(current: {state.get('status')})")
    require(current_branch(project) == state["feature_branch"], "Check out the run's feature branch first")
    from .baseline import _git
    if _git(project, "diff", "--cached", "--no-renames", "--name-only", "-z").strip("\0"):
        raise StoryLoopError("暂存区有候选改动；先用 git restore --staged -- <候选路径> 取消暂存，"
                             "核对工作区后重试修复命令", code="GIT_BLOCKED")
    from .failure_attribution import characterization_oracle
    require(not characterization_oracle(state),
            f"特征化测试与合同冲突；请作废本次变更并运行 storyloop change {project} "
            f"--supersede {change_id} --reason <原因> --message <重新描述需求> 重开")
    return change, state


def _prove_repaired_suite(project: Path, change: dict, state: dict, commit: dict,
                          reuse: dict | None = None, services: LoopServices | None = None) -> dict:
    change_id = change["change_id"]
    try:
        from .smoke import load_smoke_manifest
        manifest = load_smoke_manifest(project, change)
        check_entry_use(project, change, manifest)
        from .oracle_strength_precheck import assess as strength_assess
        precheck = strength_assess(project, change, state, services, manifest,
                                   load_project_config(project))
        if precheck is not None:
            state["oracle_strength_precheck"] = precheck
        red = read_only_call(
            project, change_id,
            lambda: run_smoke_isolated(project, change, expect_red=True, at_commit=state["contract_commit"]),
            allowed_evidence=RED_EVIDENCE,
        )
        if precheck is not None:
            from .oracle_strength_precheck import stamp_red
            red = stamp_red(project, change_id, precheck)
    except StoryLoopError as exc:
        if exc.code not in {"EXISTING_GREEN", "ORACLE_DEFECT", "NEEDS_INPUT"}:
            raise
        from .consistency import invalidate_consistency
        invalidate_consistency(state, replan=True)
        if state["repairs"][-1].get("automatic"):
            state["repairs"][-1]["failure_summary"] = _failure_summary(exc)
        return _unproven_suite(project, state, exc)
    from .consistency import invalidate_consistency
    from .design_approval import rebind_repaired_plan
    retained = rebind_repaired_plan(project, change, state, reuse)
    invalidate_consistency(state, replan=not retained)
    if retained:
        state['planning']['review_repaired_suite'] = True
    for key in ("questions", "error", "pending", "acceptance", "next", "routed", "stop", "mode"):
        state.pop(key, None)
    ensure_baseline(project, state, change["product"], run_commands_at_commit)
    transition(project, state, "IMPLEMENTING", event="repair_red",
               updates={"attempt": 0, "red_evidence_sha256": red["evidence_sha256"]})
    save_run(project, state)
    return {"status": "ACCEPTANCE_REPAIRED", "change_id": change_id, "commit": commit["commit"],
            "next": f"storyloop run {shlex.quote(str(project))} --change {change_id}"}


def _failure_summary(error: StoryLoopError) -> str:
    from .processutil import redact
    results = error.details.get("results") or []
    parts = [f"{item.get('id', '验收用例')}: " + str(item.get("stderr") or item.get("stdout") or "")[-1000:]
             for item in results[:3] if isinstance(item, dict)]
    return redact(" | ".join(parts) or str(error))[-1500:]


def _tracked_under(project: Path, relative: str) -> list[str]:
    from .baseline import _git
    return [path for path in _git(project, "ls-files", "-z", "--", relative).split("\0") if path]


def _commit_repair_paths(project: Path, paths: list[str], message: str) -> dict:
    try:
        return commit_paths(project, paths, message)
    except StoryLoopError as exc:
        if str(exc) == "目标路径没有可提交的改动":
            raise StoryLoopError("目标路径没有改动；请先修正判据文件，再重新运行修复命令",
                                 code="GIT_BLOCKED") from exc
        raise


def _unproven_suite(project: Path, state: dict, error: StoryLoopError) -> dict:
    """The repaired suite is installed and committed but did not prove RED: record that as the stop.

    The old RED fingerprint belongs to the replaced suite, so it goes; the run cannot implement until a
    suite proves RED again (another repair, or `run` re-proving it on the contract commit).
    """
    state.pop("red_evidence_sha256", None)
    state["attempt"] = 0
    for key in ("questions", "pending", "next", "routed", "stop", "mode"):
        state.pop(key, None)
    details = {"error": str(error), "acceptance": {"status": error.code, **error.details}}
    if error.code == "NEEDS_INPUT":
        details["questions"] = (error.details.get("precheck") or {}).get("questions") or []
    return pause(project, state, error.code, **details)


def _last_smoke_evidence(project: Path, change_id: str, state: dict) -> dict:
    """Tails of the latest acceptance output, so the repairing agent sees what actually happened."""
    if state.get("status") in {"EXISTING_GREEN", "ORACLE_DEFECT"} and isinstance(state.get("acceptance"), dict):
        return state["acceptance"]  # green before implementation: the suite must assert the new behaviour
    for failure in reversed(state.get("failures") or []):
        smoke = failure.get("acceptance") or failure.get("smoke")
        if isinstance(smoke, dict) and smoke.get("results"):
            return {"status": smoke.get("status"), "results": [
                {"id": r.get("id"), "status": r.get("status"), "stdout": str(r.get("stdout", ""))[-6000:],
                 "stderr": str(r.get("stderr", ""))[-6000:],
                 "diagnostics": (r.get("counts") or {}).get("diagnostics", [])[:20]}
                for r in smoke["results"]]}
    return {}
