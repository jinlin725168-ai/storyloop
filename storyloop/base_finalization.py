"""Crash recoverable baseline promotion for an accepted base run."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .base_upgrade import _dict_digest, _next_baseline, _next_version
from .baseline import _git, load_baseline, load_project_config
from .git_command import git_is_ancestor
from .models import StoryLoopError, require
from .project_git import current_branch
from .run_state import save_run
from .storage import load_json, write_json


def finalize_base_baseline(project: Path, change: dict, state: dict) -> dict:
    """Promote the merged commit exactly once, resuming either write window."""
    project = Path(project).resolve()
    head = state["integration_commit"]
    require(current_branch(project) == state["source_branch"]
            and _git(project, "rev-parse", "HEAD").strip() == head,
            "Integrated base source branch changed before finalization")
    require(not _git(project, "status", "--porcelain=v1", "--untracked-files=all").strip(),
            "Base finalization needs a clean committed source branch")
    baseline_path = project / ".storyloop/baseline.json"
    config_path = project / ".storyloop/config.json"
    receipt_path = project / ".storyloop/state/base-upgrades" / f"{change['change_id']}.json"
    baseline = load_baseline(project)
    config = load_project_config(project)
    locked_receipt = _locked_receipt(project, change, state, baseline, config,
                                     head, baseline_path, config_path)
    receipt = _load_or_repair_receipt(receipt_path, locked_receipt, baseline)
    require(receipt.get("version") == 2 and receipt.get("proposal_id") == change["change_id"]
            and receipt.get("contract_digest") == state["contract_digest"]
            and receipt.get("integration_commit") == head
            and receipt.get("status") in {"ACCEPTING", "ACCEPTED"},
            "Base lifecycle receipt does not match the approved run")
    require(hashlib.sha256(config_path.read_bytes()).hexdigest() == receipt["config_sha256"],
            "Project configuration changed during base finalization")
    applied = (baseline["source_head"] == head
               and baseline["baseline_version"] == receipt["new_baseline_version"]
               and _dict_digest(baseline) == receipt["candidate_baseline_digest"])
    if not applied:
        _require_baseline(receipt["status"] == "ACCEPTING"
                          and baseline["source_head"] == receipt["baseline_source_head"]
                          and baseline["baseline_version"] == receipt["old_baseline_version"]
                          and (not receipt["baseline_sha256"]
                               or hashlib.sha256(baseline_path.read_bytes()).hexdigest()
                               == receipt["baseline_sha256"]),
                          "Current baseline does not match the accepted base transition")
        candidate = _next_baseline(project, baseline, config["profile"], head,
                                   receipt["new_baseline_version"])
        _require_baseline(_dict_digest(candidate) == receipt["candidate_baseline_digest"],
                          "Base candidate baseline changed during recovery")
        write_json(baseline_path, candidate, max_bytes=8_000_000)
    if receipt["status"] != "ACCEPTED":
        receipt["status"] = "ACCEPTED"
        write_json(receipt_path, receipt, max_bytes=4_000_000)
    _finish_memory_landings(project, change["change_id"])
    return receipt


def _load_or_repair_receipt(receipt_path: Path, locked_receipt: dict,
                            baseline: dict) -> dict:
    try:
        receipt = load_json(receipt_path) if receipt_path.exists() else None
    except StoryLoopError:
        receipt = None
    if (receipt is None or not _receipt_matches(receipt, locked_receipt)
            or (receipt.get("status") == "ACCEPTED"
                and baseline["baseline_version"] == locked_receipt["old_baseline_version"])):
        receipt = dict(locked_receipt)
        write_json(receipt_path, receipt, max_bytes=4_000_000)
    return receipt


def _receipt_matches(receipt: dict, locked: dict) -> bool:
    return (receipt.get("status") in {"ACCEPTING", "ACCEPTED"}
            and {key: value for key, value in receipt.items() if key != "status"}
            == {key: value for key, value in locked.items() if key != "status"})


def _require_baseline(condition: bool, message: str) -> None:
    if not condition:
        raise StoryLoopError(message, code="BASELINE_MISMATCH")


def _locked_receipt(project: Path, change: dict, state: dict, baseline: dict, config: dict,
                    head: str, baseline_path: Path, config_path: Path) -> dict:
    locked = state.get("base_finalization_receipt")
    digest = state.get("base_finalization_receipt_sha256")
    if (isinstance(locked, dict) and isinstance(digest, str)
            and _dict_digest(locked) == digest):
        return locked
    approved = change["approval"]["versions"]["baseline_version"]
    if baseline["baseline_version"] == approved:
        locked = _new_receipt(project, change, state, baseline, config, head,
                              baseline_path, config_path)
    else:
        _require_baseline(baseline["baseline_version"] == _next_version(approved),
                          "Current baseline version differs from the approved transition")
        locked = _revalidated_applied_receipt(project, change, state, baseline, config,
                                               head, config_path, approved)
    state["base_finalization_receipt"] = locked
    state["base_finalization_receipt_sha256"] = _dict_digest(locked)
    save_run(project, state)
    return locked


def _revalidated_applied_receipt(project: Path, change: dict, state: dict,
                                 baseline: dict, config: dict, head: str,
                                 config_path: Path, approved: str) -> dict:
    version = _next_version(approved)
    _require_baseline(baseline["baseline_version"] == version
                      and baseline["source_head"] == head
                      and baseline["source_branch"] == state["source_branch"]
                      and git_is_ancestor(project, state["source_head"], head),
                      "Existing baseline cannot be revalidated against the approved base run")
    candidate = _next_baseline(project, baseline, config["profile"], head, version)
    _require_baseline(_dict_digest(candidate) == _dict_digest(baseline),
                      "Existing baseline differs from the verified integration commit")
    return {"version": 2, "proposal_id": change["change_id"], "status": "ACCEPTING",
            "contract_digest": state["contract_digest"], "integration_commit": head,
            "source_branch": state["source_branch"], "baseline_source_head": state["source_head"],
            "old_baseline_version": approved, "new_baseline_version": version,
            "baseline_sha256": "", "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
            "candidate_baseline_digest": _dict_digest(candidate)}


def _new_receipt(project: Path, change: dict, state: dict, baseline: dict, config: dict,
                 head: str, baseline_path: Path, config_path: Path) -> dict:
    _require_baseline(baseline["baseline_version"]
                      == change["approval"]["versions"]["baseline_version"],
                      "Current baseline version differs from the approved baseline version")
    _require_baseline(baseline["source_branch"] == state["source_branch"]
                      and git_is_ancestor(project, baseline["source_head"], state["source_head"]),
                      "Base finalization source no longer descends from the locked baseline")
    version = _next_version(baseline["baseline_version"])
    candidate = _next_baseline(project, baseline, config["profile"], head, version)
    return {"version": 2, "proposal_id": change["change_id"],
            "status": "ACCEPTING", "contract_digest": state["contract_digest"],
            "integration_commit": head, "source_branch": state["source_branch"],
            "baseline_source_head": baseline["source_head"],
            "old_baseline_version": baseline["baseline_version"],
            "new_baseline_version": version,
            "baseline_sha256": hashlib.sha256(baseline_path.read_bytes()).hexdigest(),
            "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
            "candidate_baseline_digest": _dict_digest(candidate)}


def _finish_memory_landings(project: Path, change_id: str) -> None:
    root = project / ".storyloop/state/base-landings"
    if not root.is_dir():
        return
    from .drafts import set_draft_status
    for path in root.glob("*.json"):
        record = load_json(path)
        if record.get("result", {}).get("base_proposal") == change_id:
            set_draft_status(project, record["ref"], "approved")
            path.unlink()
