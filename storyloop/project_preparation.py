"""Contract materialization and immutable pre-implementation evidence."""
from __future__ import annotations
from pathlib import Path
from typing import TYPE_CHECKING
from .approvals import materialize_approved_change
from .baseline import _git
from .models import StoryLoopError, require
from .run_transitions import transition
from .run_guards import read_red_evidence
from .lesson_store import lessons_digest
from .openspec_docs import validate_with_cli
from .project_git import commit_paths
from .guarded_calls import read_only_call
from .project_loop_support import relative_files, save_run
from .run_stops import pause
from .oracle_counts import ensure_baseline
from .semantic_repair import answer_with_repair
from .smoke import (RED_EVIDENCE, install_smoke_suite, load_smoke_manifest, protection_path,
                    validate_suite, without_manifest_echo, suite_root as acceptance_suite_root)
from .smoke_runner import run_smoke_isolated
if TYPE_CHECKING:
    from .run_services import LoopServices


def _prepare_acceptance(
    project: Path, change: dict, services: LoopServices, state: dict,
) -> dict | None:
    change_id = change["change_id"]
    contract_root = materialize_approved_change(project, change_id)
    state.setdefault("openspec", {})["delta"] = validate_with_cli(project, "change", change_id)
    smoke_state = protection_path(project, change_id)
    if smoke_state.exists():
        load_smoke_manifest(project, change)
    else:
        def validate(answer: object) -> None:
            require(isinstance(answer, dict) and {"files", "manifest"} <= set(answer) <= {"files", "manifest", "lessons_used", "errata"},
                    "Smoke agent returned an invalid contract")
            validate_suite(change, answer["files"], answer["manifest"])

        generated = answer_with_repair(
            lambda request: read_only_call(
                project, change_id, lambda: services.generate_smoke(project, request)),
            change, validate)
        install_smoke_suite(project, change, without_manifest_echo(generated["files"]), generated["manifest"])
    smoke_root = acceptance_suite_root(project, change_id)
    paths = relative_files(project, [contract_root, smoke_root])
    if state["status"] == "PREPARING":
        transition(project, state, "CONTRACT_READY", event="contract_ready")
        save_run(project, state)
    if state["status"] == "CONTRACT_READY":
        contract_commit = _ensure_contract_commit(project, state, paths)
        transition(project, state, "CONTRACT_COMMITTED", event="contract_committed", updates={
                      "contract_commit": contract_commit,
                      **_lesson_snapshot(project, contract_commit)})
        save_run(project, state)
    try:
        red = _ensure_red_evidence(project, change, state["contract_commit"])
    except StoryLoopError as exc:
        if exc.code not in {"EXISTING_GREEN", "ORACLE_DEFECT"}:
            raise
        return pause(project, state, exc.code, error=str(exc),
                     acceptance={"status": exc.code, **exc.details})
    ensure_baseline(project, state, change["product"])  # sampled once, on the code before implementation
    for key in ("acceptance", "error", "next"):
        state.pop(key, None)
    transition(project, state, "IMPLEMENTING", event="red_ready",
               updates={"red_evidence_sha256": red["evidence_sha256"]})
    save_run(project, state)
    return None


def _lesson_snapshot(project: Path, commit: str) -> dict:
    """Freeze invalid-layer diagnostics without hiding repository or system failures."""
    try:
        return {"lessons_digest": lessons_digest(project, commit)}
    except StoryLoopError as exc:
        # Resolution failures wrap a Git error; only a directly identified bad layer
        # can be isolated after the immutable contract has been committed.
        if exc.code != "LESSON_INVALID" or exc.__cause__ is not None:
            raise
        return {"lessons_digest": None, "lessons_digest_error": f"LESSON_INVALID: {exc}"}


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


def _ensure_red_evidence(project: Path, change: dict, contract_commit: str) -> dict:
    """RED is proven on the contract commit plus the current suite: never on the workspace, which after a
    stop may already hold implementation edits that would make the new behaviour look present."""
    change_id = change["change_id"]
    path = project / ".storyloop/evidence" / change_id / RED_EVIDENCE
    if path.exists():
        return read_red_evidence(project, change_id)
    return read_only_call(
        project, change_id,
        lambda: run_smoke_isolated(project, change, expect_red=True, at_commit=contract_commit),
        allowed_evidence=RED_EVIDENCE,
    )
