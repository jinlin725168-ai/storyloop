"""`storyloop acceptance-repair`: the only channel for fixing defects in a change's acceptance suite.

The implementer never touches the suite. A human judges that a failure is a test defect (or that
the suite was too weak to prove RED) and says why; the acceptance role repairs its own suite and
RED is re-proven at the contract commit, voiding earlier attempts.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

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
from .planning_choice import selected_option

if TYPE_CHECKING:  # the loop imports this module; the services type is only an annotation
    from .run_services import LoopServices





def repair_smoke(project: Path, change_id: str, reason: str, services: LoopServices | None = None,
                 now=None, *, suite: str = "acceptance") -> dict:
    """Let the smoke owner fix test defects in its own suite, then re-prove RED at the contract commit.

    The implementer never touches the suite; a human must judge that the failure is a test
    defect and say why. The reason is recorded, the old attempts are voided, and the new suite
    must still fail on the tree as it was before any implementation.
    """
    project = Path(project).resolve()
    change_id = identifier(change_id, "change id")
    reason = text(reason, "repair reason")
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
    if suite == "characterization":
        return repair_characterization(project, change, state, reason, now=now)
    require(suite == "acceptance", "Unknown repair suite")
    from .failure_attribution import characterization_oracle
    require(not characterization_oracle(state),
            "缺陷位于特征化测试；请先修正该测试，再运行 acceptance-repair --suite characterization")
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
    from .consistency import invalidate_consistency
    invalidate_consistency(state, replan=True)
    state.setdefault("repairs", []).append({"at": moment, "reason": reason, "commit": commit["commit"],
                                            "voided_attempts": int(state.get("attempt", 0))})
    return _prove_repaired_suite(project, change, state, commit)


def _prove_repaired_suite(project: Path, change: dict, state: dict, commit: dict,
                          red: dict | None = None) -> dict:
    change_id = change["change_id"]
    try:
        if red is None:
            red = _reprove_red(project, change, state)
    except StoryLoopError as exc:
        if exc.code not in {"EXISTING_GREEN", "ORACLE_DEFECT"}:
            raise
        return _unproven_suite(project, state, exc)
    for key in ("questions", "error", "pending", "acceptance", "next", "routed", "stop", "mode"):
        state.pop(key, None)
    ensure_baseline(project, state, change["product"], run_commands_at_commit)
    transition(project, state, "IMPLEMENTING", event="repair_red",
               updates={"attempt": 0, "red_evidence_sha256": red["evidence_sha256"]})
    save_run(project, state)
    return {"status": "ACCEPTANCE_REPAIRED", "change_id": change_id, "commit": commit["commit"],
            "next": f"storyloop run <project> --change {change_id}"}


def _reprove_red(project: Path, change: dict, state: dict) -> dict:
    return read_only_call(project, change["change_id"],
                          lambda: run_smoke_isolated(project, change, expect_red=True,
                                                     at_commit=state["contract_commit"]),
                          allowed_evidence=RED_EVIDENCE)


def repair_characterization(project: Path, change: dict, state: dict, reason: str, now=None) -> dict:
    """Commit a human correction only after its locked tests pass against old code."""
    verified = _verify_characterization_repair(project, change, state)
    if isinstance(verified, dict):
        return verified
    tests, kept, changed, locks, unit, red = verified
    _discard_characterization_candidate(project, state, set(tests))
    previous = state["characterization"]
    previous["tests"] = kept
    if locks is not None:
        previous["locks"] = locks
    else:
        state.pop("characterization", None)  # replacement locks are generated on the next run
    from .oracle_counts import executed_list
    state.setdefault("oracle", {}).setdefault("baseline", {})["unit"] = executed_list(unit)
    moment = (now or (lambda: datetime.now(timezone.utc)))().astimezone(timezone.utc).isoformat(timespec="seconds")
    from .consistency import invalidate_consistency
    invalidate_consistency(state, replan=True)
    from .design_approval import clear_l3_replan_checkpoint
    clear_l3_replan_checkpoint(state)
    voided = int(state.get("attempt", 0))
    for key in ("questions", "error", "pending", "acceptance", "next", "routed", "stop", "mode"):
        state.pop(key, None)
    ensure_baseline(project, state, change["product"], run_commands_at_commit)
    transition(project, state, "IMPLEMENTING", event="repair_red",
               updates={"attempt": 0, "red_evidence_sha256": red["evidence_sha256"]})
    commit = _commit_repair_paths(project, changed, f"test({change['change_id']}): repair characterization")
    previous.setdefault("commits", []).append(commit["commit"])
    state.setdefault("repairs", []).append({"at": moment, "reason": reason, "suite": "characterization",
                                            "commit": commit["commit"], "voided_attempts": voided})
    try:
        save_run(project, state)
    except Exception:
        from .baseline import _git
        # A failed atomic state write must not strand a committed test correction outside state.
        _git(project, "reset", "--soft", "HEAD^")
        _git(project, "reset", "-q", "--", *changed)
        raise
    return {"status": "CHARACTERIZATION_REPAIRED", "change_id": change["change_id"],
            "commit": commit["commit"], "next": f"storyloop run <project> --change {change['change_id']}"}


def _verify_characterization_repair(project: Path, change: dict, state: dict) -> tuple | dict:
    """Prove corrected locks and RED before changing the candidate or Git history."""
    from .baseline import _git, load_project_config
    from .characterization_baseline import run_characterization_baseline
    from .characterization_locks import capture_locks
    from .project_profile import layer_commands
    from .failure_attribution import characterization_oracle

    require(state.get("status") in {"ORACLE_DEFECT", "EXISTING_GREEN"} and characterization_oracle(state),
            "停止证据没有指向本变更特征化提交的测试；请修正验收套件或核对审查归属")
    previous = state["characterization"]
    tests = sorted(set(previous["tests"]))
    changed = [path for path in _git(project, "diff", "--no-renames", "--name-only", "-z",
                                     "--", *tests).split("\0") if path]
    require(bool(changed), "特征化测试没有改动；请按审查理由先修正测试")
    config = load_project_config(project)
    kept = [path for path in tests if (project / path).is_file()]
    coverage = {path: [test for test in artifacts if test in kept]
                for path, artifacts in previous.get("locks", {}).items()}
    coverage = {path: artifacts for path, artifacts in coverage.items() if artifacts}
    pending = {"tests": kept, "changed": changed, "touched": previous["touched"],
               "coverage": coverage}
    locks = capture_locks(project, config, pending) if coverage else None
    unit = run_characterization_baseline(project, layer_commands(config["profile"], "unit"),
                                         config["check_timeout_seconds"], pending)
    require(unit["status"] == "PASSED", "修正后的特征化测试在旧代码上仍不绿；请继续修正")
    if locks is not None:
        require(locks == capture_locks(project, config, pending), "重证期间特征化测试发生变化")
    # Prove every fallible oracle gate before changing Git history. A failed RED keeps the
    # human's correction in the workspace so the same repair command remains usable.
    try:
        red = _reprove_red(project, change, state)
    except StoryLoopError as exc:
        if exc.code not in {"EXISTING_GREEN", "ORACLE_DEFECT"}:
            raise
        return _unproven_suite(project, state, exc)
    return tests, kept, changed, locks, unit, red


def _discard_characterization_candidate(project: Path, state: dict, tests: set[str]) -> None:
    """Abandon only candidate paths named by the saved selected option."""
    from .baseline import _git
    from .path_security import safe_target

    option = selected_option(state["planning"]["plan"])
    touched = set(option.get("touched") or [])
    creates = set(option.get("creates") or [])
    tracked = {path for path in _git(project, "diff", "--no-renames", "--name-only", "-z").split("\0") if path}
    untracked = {path for path in _git(project, "ls-files", "--others", "--exclude-standard", "-z").split("\0") if path}
    candidate_tracked = tracked - tests
    from .baseline import load_project_config
    from .project_profile import matches_test_root
    from .project_loop_guards import scratch_paths, _is_scratch
    roots = load_project_config(project)["profile"].get("test_roots", [])
    scratch = scratch_paths(project)
    def is_scratch(path: str) -> bool:
        return _is_scratch(path, scratch)
    extra = sorted(path for path in (candidate_tracked - touched) | (untracked - creates)
                   if not matches_test_root(path, roots) and not is_scratch(path))
    require(not extra, "候选之外还有未提交改动；请先人工核对并清理：" + ", ".join(extra[:8]))
    for path in sorted(path for path in untracked if not is_scratch(path)):
        safe_target(project, path, "candidate file").unlink()
    if candidate_tracked:
        _git(project, "restore", "--worktree", "--", *sorted(candidate_tracked))


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
    return pause(project, state, error.code, error=str(error),
                 acceptance={"status": error.code, **error.details})


def _last_smoke_evidence(project: Path, change_id: str, state: dict) -> dict:
    """Tails of the latest acceptance output, so the repairing agent sees what actually happened."""
    if state.get("status") in {"EXISTING_GREEN", "ORACLE_DEFECT"} and isinstance(state.get("acceptance"), dict):
        return state["acceptance"]  # green before implementation: the suite must assert the new behaviour
    for failure in reversed(state.get("failures") or []):
        smoke = failure.get("acceptance") or failure.get("smoke")
        if isinstance(smoke, dict) and smoke.get("results"):
            return {"status": smoke.get("status"), "results": [
                {"id": r.get("id"), "status": r.get("status"), "stdout": str(r.get("stdout", ""))[-6000:],
                 "stderr": str(r.get("stderr", ""))[-6000:]} for r in smoke["results"]]}
    return {}
