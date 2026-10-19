"""Contract materialization and immutable pre-implementation evidence."""
from __future__ import annotations
import hashlib
from pathlib import Path
from typing import TYPE_CHECKING
from .approvals import materialize_approved_change
from .baseline import _git
from .models import StoryLoopError, require
from .openspec_docs import validate_with_cli
from .project_git import commit_paths
from .project_loop_guards import read_only_call
from .project_loop_support import pause, relative_files, save_run
from .oracle_counts import ensure_baseline
from .semantic_repair import answer_with_repair
from .smoke import (RED_EVIDENCE, install_smoke_suite, load_smoke_manifest, protection_path,
                    validate_suite, without_manifest_echo, suite_root as acceptance_suite_root)
from .smoke_runner import run_smoke_isolated
from .storage import load_json
if TYPE_CHECKING:
    from .project_loop import LoopServices


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
        def validate(answer: object) -> None:
            require(isinstance(answer, dict) and set(answer) == {"files", "manifest"},
                    "Smoke agent returned an invalid contract")
            validate_suite(change, answer["files"], answer["manifest"])

        generated = answer_with_repair(
            lambda request: read_only_call(
                project, change_id, lambda: services.generate_smoke(project, request)),
            change, validate)
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
