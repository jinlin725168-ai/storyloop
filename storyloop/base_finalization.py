"""Crash recoverable baseline promotion for an accepted base run."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .base_upgrade import _dict_digest, _next_baseline, _next_version
from .baseline import _git, load_baseline, load_project_config
from .models import require
from .project_git import current_branch
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
    if receipt_path.exists():
        receipt = load_json(receipt_path)
    else:
        require(baseline["source_head"] == state["source_head"],
                "Base finalization source no longer matches the locked baseline")
        version = _next_version(baseline["baseline_version"])
        candidate = _next_baseline(project, baseline, config["profile"], head, version)
        receipt = {"version": 2, "proposal_id": change["change_id"],
                   "status": "ACCEPTING", "contract_digest": state["contract_digest"],
                   "integration_commit": head, "source_branch": state["source_branch"],
                   "baseline_source_head": baseline["source_head"],
                   "old_baseline_version": baseline["baseline_version"],
                   "new_baseline_version": version,
                   "baseline_sha256": hashlib.sha256(baseline_path.read_bytes()).hexdigest(),
                   "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
                   "candidate_baseline_digest": _dict_digest(candidate)}
        write_json(receipt_path, receipt, max_bytes=4_000_000)
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
        require(receipt["status"] == "ACCEPTING"
                and baseline["source_head"] == receipt["baseline_source_head"]
                and baseline["baseline_version"] == receipt["old_baseline_version"]
                and hashlib.sha256(baseline_path.read_bytes()).hexdigest() == receipt["baseline_sha256"],
                "Current baseline does not match the accepted base transition")
        candidate = _next_baseline(project, baseline, config["profile"], head,
                                   receipt["new_baseline_version"])
        require(_dict_digest(candidate) == receipt["candidate_baseline_digest"],
                "Base candidate baseline changed during recovery")
        write_json(baseline_path, candidate, max_bytes=8_000_000)
    if receipt["status"] != "ACCEPTED":
        receipt["status"] = "ACCEPTED"
        write_json(receipt_path, receipt, max_bytes=4_000_000)
    _finish_memory_landings(project, change["change_id"])
    return receipt


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
