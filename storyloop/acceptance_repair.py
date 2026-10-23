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
from .project_loop_guards import read_only_call
from .project_loop_support import relative_files, run_path, save_run
from .run_stops import pause
from .run_transitions import transition, REPAIRABLE
from .smoke import RED_EVIDENCE, RESULT_EVIDENCE, install_smoke_suite, without_manifest_echo
from .smoke import suite_relative, suite_root as acceptance_suite_root
from .smoke_runner import run_smoke_isolated
from .storage import load_json

if TYPE_CHECKING:  # the loop imports this module; the services type is only an annotation
    from .run_services import LoopServices





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
    path = run_path(project, change_id)
    require(path.exists(), "Repair needs a started run with a committed contract")
    state = load_json(path)
    require(state.get("status") in REPAIRABLE and state.get("contract_commit"),
            f"Acceptance repair needs a committed contract that has not completed "
            f"(current: {state.get('status')})")
    require(current_branch(project) == state["feature_branch"], "Check out the run's feature branch first")
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
    commit = commit_paths(project, paths, f"test({change_id}): repair acceptance suite")
    moment = (now or (lambda: datetime.now(timezone.utc)))().astimezone(timezone.utc).isoformat(timespec="seconds")
    from .consistency import invalidate_consistency
    invalidate_consistency(state, replan=True)
    state.setdefault("repairs", []).append({"at": moment, "reason": reason, "commit": commit["commit"],
                                            "voided_attempts": int(state.get("attempt", 0))})
    return _prove_repaired_suite(project, change, state, commit)


def _prove_repaired_suite(project: Path, change: dict, state: dict, commit: dict) -> dict:
    change_id = change["change_id"]
    try:
        red = read_only_call(
            project, change_id,
            lambda: run_smoke_isolated(project, change, expect_red=True, at_commit=state["contract_commit"]),
            allowed_evidence=RED_EVIDENCE,
        )
    except StoryLoopError as exc:
        if exc.code not in {"EXISTING_GREEN", "ORACLE_DEFECT"}:
            raise
        return _unproven_suite(project, state, exc)
    for key in ("questions", "error", "pending", "acceptance", "next", "routed", "stop", "mode"):
        state.pop(key, None)
    ensure_baseline(project, state, change["product"])  # a stop before RED never sampled one
    transition(project, state, "IMPLEMENTING", event="repair_red",
               updates={"attempt": 0, "red_evidence_sha256": red["evidence_sha256"]})
    save_run(project, state)
    return {"status": "ACCEPTANCE_REPAIRED", "change_id": change_id, "commit": commit["commit"],
            "next": f"storyloop run <project> --change {change_id}"}


def _tracked_under(project: Path, relative: str) -> list[str]:
    from .baseline import _git
    return [path for path in _git(project, "ls-files", "-z", "--", relative).split("\0") if path]


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
