"""Merge an accepted blocking base into the original feature branch once."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .base_upgrade import _dict_digest
from .baseline import _git
from .changes import load_change
from .git_command import git_is_ancestor
from .models import StoryLoopError, require
from .project_git import current_branch
from .run_state import run_path, save_run
from .storage import load_json


def merge_blocking_base(project: Path, state: dict, baseline: dict) -> None:
    """Require the linked E1 receipt, then merge without rewriting the feature branch."""
    head = baseline["source_head"]
    _require_accepted_base(project, state, baseline)
    require(current_branch(project) == state["feature_branch"]
            and not _git(project, "status", "--porcelain=v1", "--untracked-files=all").strip(),
            "Feature branch must be clean before merging the accepted base")
    parents = _git(project, "rev-list", "--parents", "-n", "1", "HEAD").split()
    if parents[1:] != [state["contract_commit"], head]:
        require(parents[0] == state["contract_commit"],
                "Feature branch changed after the blocking base stop")
        try:
            _git(project, "merge", "--no-ff", "--no-edit", "--no-verify", state["source_branch"])
        except StoryLoopError as exc:
            raise StoryLoopError(f"Base merge conflict or failure: {exc}",
                                 code="WORKSPACE_TAINTED") from exc
    state.setdefault("base_upgrades", []).append({
        "baseline_version": baseline["baseline_version"], "from": state["source_head"],
        "to": head})
    state["source_head"] = head
    save_run(project, state)


def _require_accepted_base(project: Path, state: dict, baseline: dict) -> None:
    base_id = state["blocked_by"]
    base = load_change(project, base_id)
    proposal_path = project / ".storyloop/base-proposals" / f"{base_id}.json"
    proposal = load_json(proposal_path)
    run = load_json(run_path(project, base_id))
    receipt = load_json(project / ".storyloop/state/base-upgrades" / f"{base_id}.json")
    require(base["status"] == run.get("status") == "PASSED"
            and proposal.get("triggered_by") == state["change_id"]
            and base.get("base_proposal_sha256") == hashlib.sha256(proposal_path.read_bytes()).hexdigest()
            and run.get("contract_digest") == receipt.get("contract_digest")
            == base["approval"]["contract_digest"], "Blocking base is not the accepted draft")
    head = baseline["source_head"]
    require(receipt.get("version") == 2 and receipt.get("status") == "ACCEPTED"
            and receipt.get("proposal_id") == base_id
            and receipt.get("integration_commit") == run.get("integration_commit") == head
            and receipt.get("new_baseline_version") == baseline["baseline_version"]
            and receipt.get("candidate_baseline_digest") == _dict_digest(baseline)
            and run.get("source_branch") == baseline["source_branch"] == state["source_branch"]
            and _git(project, "rev-parse", state["source_branch"]).strip() == head
            and git_is_ancestor(project, state["source_head"], head),
            "Blocking base receipt or source branch differs from the current baseline")
