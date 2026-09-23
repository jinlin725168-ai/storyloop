"""Explicit, regression-gated upgrades of the logical project foundation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath

from .baseline import (_foundation_paths, _git, _sha256, _tracked_files,
                       load_baseline, load_project_config)
from .models import identifier, require, text
from .path_security import repository_relative
from .project_boundaries import _write_base_proposal
from .project_profile import is_ci_path
from .project_profile import layer_commands
from .smoke_runner import run_commands_isolated
from .system_smoke import adopt_system_smoke, run_system_smoke_isolated
from .storage import load_json, locked, write_json


def propose_base_change(project: Path, paths: list[str], reason: str,
                        allow_mutable: bool = False) -> dict:
    """Open a BASE proposal for a planned foundation change (no feature run needed).

    `allow_mutable` is for upstream synchronisation, where the imported tree legitimately
    touches files inside module roots as well; such paths are recorded as UPSTREAM_MUTABLE.
    """
    project = Path(project).resolve()
    baseline = load_baseline(project)
    reason = text(reason, "base change reason")
    require(isinstance(paths, list) and bool(paths), "Base proposal needs at least one --path")
    changes = []
    for item in paths:
        relative = repository_relative(item, "base path")
        require(not is_ci_path(relative), "CI configuration paths cannot be changed through StoryLoop")
        # Same precedence as the boundary audit: an integration point (a nested pom or
        # package.json in a monorepo) is base even when it sits inside a module root.
        integration = relative in baseline["integration_paths"]
        mutable = not integration and any(_inside(relative, root) for root in baseline["module_roots"])
        require(allow_mutable or not mutable,
                f"{relative} is inside a mutable module root; change it through a feature, "
                "or pass --allow-mutable for an upstream sync")
        if mutable:
            category = "UPSTREAM_MUTABLE"
        elif integration:
            category = "INTEGRATION_CHANGE"
        else:
            category = "FOUNDATION_CHANGE"
        changes.append({"path": relative, "category": category})
    path = _write_base_proposal(project, "manual", changes, baseline, reason)
    return {"status": "PROPOSED", "change_id": path.stem, "proposal": str(path),
            "paths": [item["path"] for item in changes],
            "next": f"commit exactly these paths on {baseline['source_branch']}, then "
                    f"storyloop base-update <project> --proposal {path.stem}"}


def _inside(relative: str, root: str) -> bool:
    normalized = root.rstrip("/")
    return relative == normalized or relative.startswith(normalized + "/")



def upgrade_baseline(project: Path, proposal_id: str) -> dict:
    """Lock an independently committed base change after strict regression checks."""
    project = Path(project).resolve()
    proposal_id = identifier(proposal_id, "base proposal id")
    with locked(project):
        return _upgrade_locked(project, proposal_id)


def _upgrade_locked(project: Path, proposal_id: str) -> dict:
    proposal_path = project / ".storyloop/base-proposals" / f"{proposal_id}.json"
    proposal = _load_proposal(proposal_path, proposal_id)
    receipt_path = _receipt_path(project, proposal_id)
    if receipt_path.exists():
        return _recover_upgrade(project, proposal_path, proposal, receipt_path)
    require(proposal["status"] == "PROPOSED", "Base proposal is already handled")
    baseline = load_baseline(project)
    _require_candidate(project, baseline, proposal)
    head = _git(project, "rev-parse", "HEAD").strip()
    receipt = _new_receipt(project, proposal, baseline, head)
    write_json(receipt_path, receipt, max_bytes=4_000_000)
    proposal.update({"status": "ACCEPTING", "old_baseline_version": baseline["baseline_version"],
                     "new_baseline_version": receipt["new_baseline_version"], "source_head": head,
                     "source_branch": baseline["source_branch"],
                     "baseline_source_head": baseline["source_head"],
                     "acceptance_digest": receipt["proposal_digest"]})
    write_json(proposal_path, proposal, max_bytes=4_000_000)
    return _recover_upgrade(project, proposal_path, proposal, receipt_path)


def _load_proposal(path: Path, proposal_id: str) -> dict:
    value = load_json(path)
    require(value.get("change_id") == proposal_id
            and value.get("status") in {"PROPOSED", "ACCEPTING", "ACCEPTED"},
            "Base proposal is missing, invalid, or already handled")
    require(isinstance(value.get("changes"), list) and bool(value["changes"]),
            "Base proposal has no changes")
    return value


def _require_candidate(project: Path, baseline: dict, proposal: dict) -> None:
    require(_git(project, "branch", "--show-current").strip() == baseline["source_branch"],
            "Base upgrade must run on the locked source branch")
    require(not _git(project, "status", "--porcelain=v1", "--untracked-files=all").strip(),
            "Base upgrade requires a clean committed worktree")
    head = _git(project, "rev-parse", "HEAD").strip()
    require(head != baseline["source_head"], "Base candidate has no new commit")
    require(baseline["source_head"] in set(_git(project, "rev-list", "HEAD").splitlines()),
            "Base candidate does not descend from the locked baseline")
    actual = _changed_since(project, baseline["source_head"])
    expected = {item["path"] for item in proposal["changes"]}
    require(actual == expected, "Committed base paths must exactly match the approved proposal")
    require(not any(is_ci_path(path) for path in actual),
            "StoryLoop base upgrades never modify existing CI configuration")


def _changed_since(project: Path, source_head: str) -> set[str]:
    output = _git(project, "diff", "--name-only", "-z", source_head, "HEAD")
    result = set()
    for raw in output.split("\0"):
        if raw:
            normalized = raw.replace("\\", "/")
            pure = PurePosixPath(normalized)
            require(not pure.is_absolute() and ".." not in pure.parts,
                    "Git reported an unsafe base path")
            result.add(normalized)
    return result


def _next_baseline(
    project: Path, baseline: dict, profile: dict, head: str, version: str,
) -> dict:
    tracked = _tracked_files(project)
    files = {relative: _sha256(project / relative) for relative in tracked}
    protected = _foundation_paths(tracked, profile)
    return {**baseline, "baseline_version": version,
            "source_head": head, "files": files, "foundation_paths": protected,
            "module_roots": profile["module_roots"],
            "integration_paths": profile["integration_paths"],
            "protected_paths": sorted(set(protected) | set(profile["protected_paths"]))}


def _recover_upgrade(
    project: Path, proposal_path: Path, proposal: dict, receipt_path: Path,
) -> dict:
    baseline = load_baseline(project)
    config = load_project_config(project)
    receipt = _load_receipt(receipt_path, proposal)
    _require_baseline_state(project, baseline, config, receipt)
    if receipt["status"] == "ACCEPTED":
        require(baseline["source_head"] == receipt["source_head"]
                and baseline["baseline_version"] == receipt["new_baseline_version"],
                "Accepted base upgrade is not reflected by the current baseline")
        if proposal["status"] != "ACCEPTED":
            proposal["status"] = "ACCEPTED"
            proposal["regression"] = receipt["regression"]
            write_json(proposal_path, proposal, max_bytes=4_000_000)
        return _upgrade_result(receipt)
    _require_recovery_candidate(project, receipt)
    regression = _regression_layers(project, config)
    require(regression["status"] == "PASSED",
            "Base upgrade requires unit and integration layers to pass (at least one must run)")
    candidate = _next_baseline(
        project, baseline, config["profile"], receipt["source_head"],
        receipt["new_baseline_version"],
    )
    require(_dict_digest(candidate) == receipt["candidate_baseline_digest"],
            "Recovered candidate baseline differs from the accepted transition")
    write_json(project / ".storyloop/baseline.json", candidate, max_bytes=8_000_000)
    adopt_system_smoke(project)  # the accepted base may include a reviewed smoke change
    receipt.update({"status": "ACCEPTED", "regression": regression})
    write_json(receipt_path, receipt, max_bytes=4_000_000)
    proposal["status"] = "ACCEPTED"
    proposal.pop("candidate_baseline", None)
    proposal["regression"] = regression
    write_json(proposal_path, proposal, max_bytes=4_000_000)
    return _upgrade_result(receipt)


def _require_recovery_candidate(project: Path, receipt: dict) -> None:
    require(_git(project, "branch", "--show-current").strip() == receipt["source_branch"],
            "Base recovery must run on the approved source branch")
    require(not _git(project, "status", "--porcelain=v1", "--untracked-files=all").strip(),
            "Base recovery requires a clean committed worktree")
    head = _git(project, "rev-parse", "HEAD").strip()
    require(head == receipt["source_head"], "Base recovery candidate changed")
    source = receipt["baseline_source_head"]
    require(isinstance(source, str) and source in set(_git(project, "rev-list", "HEAD").splitlines()),
            "Base recovery candidate does not descend from its locked baseline")
    expected = set(receipt["approved_paths"])
    actual = _changed_since(project, source)
    require(actual == expected, "Base recovery paths differ from the approved proposal")
    require(not any(is_ci_path(path) for path in actual),
            "StoryLoop base upgrades never modify existing CI configuration")


def _next_version(version: str) -> str:
    major, minor, patch = (int(item) for item in version.split("."))
    return f"{major}.{minor}.{patch + 1}"


def _new_receipt(project: Path, proposal: dict, baseline: dict, head: str) -> dict:
    baseline_path = project / ".storyloop/baseline.json"
    config_path = project / ".storyloop/config.json"
    config = load_project_config(project)
    version = _next_version(baseline["baseline_version"])
    candidate = _next_baseline(project, baseline, config["profile"], head, version)
    return {
        "version": 1, "proposal_id": proposal["change_id"], "status": "ACCEPTING",
        "proposal_digest": _proposal_digest(proposal),
        "baseline_sha256": hashlib.sha256(baseline_path.read_bytes()).hexdigest(),
        "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
        "candidate_baseline_digest": _dict_digest(candidate),
        "baseline_source_head": baseline["source_head"],
        "old_baseline_version": baseline["baseline_version"],
        "new_baseline_version": version,
        "source_branch": baseline["source_branch"], "source_head": head,
        "approved_paths": sorted(item["path"] for item in proposal["changes"]),
    }


def _load_receipt(path: Path, proposal: dict) -> dict:
    receipt = load_json(path)
    require(receipt.get("proposal_id") == proposal["change_id"]
            and receipt.get("status") in {"ACCEPTING", "ACCEPTED"},
            "Base acceptance receipt is invalid or already handled")
    require(receipt.get("proposal_digest") == _proposal_digest(proposal),
            "Base proposal changed after acceptance started")
    require(proposal.get("acceptance_digest", receipt["proposal_digest"])
            == receipt["proposal_digest"], "Base acceptance digest changed")
    return receipt


def _require_baseline_state(
    project: Path, baseline: dict, config: dict, receipt: dict,
) -> None:
    config_path = project / ".storyloop/config.json"
    require(hashlib.sha256(config_path.read_bytes()).hexdigest() == receipt["config_sha256"],
            "Project configuration changed after base acceptance started")
    old = (baseline["source_head"] == receipt["baseline_source_head"]
           and baseline["baseline_version"] == receipt["old_baseline_version"]
           and hashlib.sha256((project / ".storyloop/baseline.json").read_bytes()).hexdigest()
           == receipt["baseline_sha256"])
    applied = (baseline["source_head"] == receipt["source_head"]
               and baseline["baseline_version"] == receipt["new_baseline_version"]
               and _dict_digest(baseline) == receipt["candidate_baseline_digest"])
    require(old or applied, "Current baseline does not match the accepted upgrade transition")
    expected = _next_baseline(
        project, baseline, config["profile"], receipt["source_head"],
        receipt["new_baseline_version"],
    )
    require(_dict_digest(expected) == receipt["candidate_baseline_digest"],
            "Candidate baseline changed after base acceptance started")


def _proposal_digest(proposal: dict) -> str:
    contract = {key: proposal.get(key) for key in (
        "change_id", "triggered_by", "baseline_version", "baseline_source_head",
        "source_branch", "changes",
    )}
    encoded = json.dumps(contract, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _dict_digest(value: dict) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _receipt_path(project: Path, proposal_id: str) -> Path:
    return project / ".storyloop/state/base-upgrades" / f"{proposal_id}.json"


def _upgrade_result(receipt: dict) -> dict:
    return {"status": "BASELINE_UPGRADED", "proposal_id": receipt["proposal_id"],
            "baseline_version": receipt["new_baseline_version"],
            "source_head": receipt["source_head"]}


def _regression_layers(project: Path, config: dict) -> dict:
    """Unit and integration layers, each with its own verdict; PASSED only if all ran green and one ran."""
    profile, timeout = config["profile"], config["check_timeout_seconds"]
    unit = run_commands_isolated(project, layer_commands(profile, "unit"), timeout)
    integration = run_commands_isolated(project, layer_commands(profile, "integration"), timeout)
    smoke = run_system_smoke_isolated(project)
    statuses = {unit["status"], integration["status"], smoke["status"]}
    status = "PASSED" if statuses <= {"PASSED", "SKIPPED"} and "PASSED" in statuses else "FAILED"
    return {"status": status, "unit": unit, "integration": integration, "smoke": smoke,
            "candidate_sha256": unit.get("candidate_sha256") or integration.get("candidate_sha256")}
