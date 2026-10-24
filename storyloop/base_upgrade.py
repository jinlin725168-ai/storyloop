"""Explicit, regression-gated upgrades of the logical project foundation."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from .baseline import (_foundation_paths, _git, _sha256, _tracked_files,
                       load_baseline, load_project_config)
from .changes import _save, load_change
from .models import identifier, require, text
from .path_security import repository_relative
from .product_contract import validate_product
from .project_boundaries import _write_base_proposal, audit_base_scope, base_scope_path_allowed
from .project_profile import base_commands_digest, is_ci_path
from .project_profile import layer_commands
from .smoke_runner import run_commands_at_commit, run_commands_isolated
from .system_smoke import adopt_system_smoke, load_system_smoke, run_system_smoke_isolated
from .storage import load_json, locked, write_json
from .lesson_store import load_lessons_at
from .lessons import scope_matches


def propose_base_change(project: Path, paths: list[str], reason: str,
                        allow_mutable: bool = False, allow_ci: bool = False,
                        scope: str | None = None) -> dict:
    """Open a BASE proposal for a planned foundation change (no feature run needed).

    `allow_mutable` is for upstream synchronisation, where the imported tree legitimately
    touches files inside module roots as well; such paths are recorded as UPSTREAM_MUTABLE.
    `allow_ci` is the only way CI configuration enters or changes the base: a human says so
    at proposal time; agents can never propose it and feature runs never touch it.
    """
    project = Path(project).resolve()
    baseline = load_baseline(project)
    reason = text(reason, "base change reason")
    require(isinstance(paths, list) and bool(paths), "Base proposal needs at least one --path")
    changes = []
    for item in paths:
        relative = repository_relative(item, "base path")
        require(not (relative.startswith(".storyloop/") or relative == ".storyloop"
                     or relative.startswith(".git/") or relative == ".git"),
                "Base proposal cannot change the control plane")
        ci = is_ci_path(relative)
        require(allow_ci or not ci, "CI configuration paths change only through a human BASE proposal "
                                    "(storyloop base-propose --allow-ci)")
        # Same precedence as the boundary audit: an integration point (a nested pom or
        # package.json in a monorepo) is base even when it sits inside a module root.
        integration = relative in baseline["integration_paths"]
        mutable = not integration and any(_inside(relative, root) for root in baseline["module_roots"])
        require(allow_mutable or not mutable,
                f"{relative} is inside a mutable module root; change it through a feature, "
                "or pass --allow-mutable for an upstream sync")
        if ci:
            category = "CI_CHANGE"
        elif mutable:
            category = "UPSTREAM_MUTABLE"
        elif integration:
            category = "INTEGRATION_CHANGE"
        else:
            category = "FOUNDATION_CHANGE"
        changes.append({"path": relative, "category": category})
    scope = scope or ("ci" if allow_ci else "upstream" if allow_mutable else "foundation")
    require(scope in {"foundation", "ci", "upstream", "skills", "knowledge"},
            "Invalid base scope")
    authorized = ((scope == "ci" and allow_ci and not allow_mutable)
                  or (scope == "upstream" and allow_mutable and not allow_ci)
                  or (scope in {"foundation", "skills", "knowledge"}
                      and not allow_ci and not allow_mutable))
    require(authorized,
            "Base scope authorization does not match proposal flags")
    require(all(base_scope_path_allowed(scope, item["path"]) for item in changes),
            "Base scope cannot include these paths")
    path = _write_base_proposal(project, "manual", changes, baseline, reason)
    draft_from_proposal(project, path.stem, scope=scope)
    return {"status": "PROPOSED", "change_id": path.stem, "proposal": str(path),
            "paths": [item["path"] for item in changes],
            "next": f"storyloop approve <project> --change {path.stem}"}


def _inside(relative: str, root: str) -> bool:
    normalized = root.rstrip("/")
    return relative == normalized or relative.startswith(normalized + "/")


def draft_from_proposal(project: Path, proposal_id: str, *, scope: str) -> dict:
    """Create the matching change state once; leave historical proposal bytes alone."""
    project = Path(project).resolve()
    proposal = load_json(project / ".storyloop/base-proposals" / f"{proposal_id}.json")
    proposal_path = project / ".storyloop/base-proposals" / f"{proposal_id}.json"
    proposal_sha256 = hashlib.sha256(proposal_path.read_bytes()).hexdigest()
    require(proposal.get("change_id") == proposal_id, "Base proposal identity mismatch")
    state_path = project / ".storyloop/state/changes" / f"{proposal_id}.json"
    if state_path.exists():
        existing = load_change(project, proposal_id)
        require(existing["product"]["nature"] == "base", "Base ID belongs to another change")
        require(existing.get("base_proposal_sha256", proposal_sha256) == proposal_sha256
                and existing["product"]["base"]["paths"]
                == [item["path"] for item in proposal["changes"]]
                and existing["product"]["goal"] == (proposal.get("reason") or "更新项目底座")
                and existing["product"]["base"]["scope"] == scope,
                "Base proposal changed after import")
        return existing
    product = {"version": 1, "goal": proposal.get("reason") or "更新项目底座",
               "non_goals": [], "questions": [], "stories": [], "nature": "base",
               "base": {"scope": scope,
                        "paths": [item["path"] for item in proposal["changes"]],
                        "must_stay_green": ["full_regression", "system_smoke", "build"]}}
    validate_product(product)
    timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    state = {"version": 1, "change_id": proposal_id, "status": "DRAFT",
             "revision": 1, "idea": product["goal"], "product": product,
             "created_at": timestamp, "updated_at": timestamp,
             "history": [], "approval": None}
    state["base_proposal_sha256"] = proposal_sha256
    _save(project, state)
    return state


def establish_old_green(project: Path, change: dict, state: dict) -> dict:
    """Prove the old source commit keeps all configured checks green before implementation."""
    project = Path(project).resolve()
    baseline, config = load_baseline(project), load_project_config(project)
    profile = config["profile"]
    require(change["product"]["nature"] == "base", "Old-green proof needs a base change")
    require(base_commands_digest(profile) == change["approval"]["base_commands_sha256"],
            "Approved base commands changed before G1")
    source = state.get("source_head", baseline["source_head"])
    timeout = config["check_timeout_seconds"]
    layers = {}
    for name in ("unit", "integration", "checks"):
        commands = layer_commands(profile, name)
        layers[name] = run_commands_at_commit(project, source, commands, timeout)
        require(not commands or layers[name]["status"] == "PASSED",
                f"Old base {name} layer is not green")
    require(any(layer_commands(profile, name) for name in layers),
            "Old base has no configured regression commands")
    manifest = load_system_smoke(project)
    require(manifest is not None, "Old base has no installed system smoke suite")
    smoke = run_commands_at_commit(project, source,
                                   [item["command"] for item in manifest["tests"]], timeout)
    require(smoke["status"] == "PASSED", "Old base system smoke is not green")
    build_commands = profile["base_build_commands"]
    build = (run_commands_at_commit(project, source, build_commands, timeout)
             if build_commands else {"status": "DECLARED_ABSENT", "results": []})
    require(build["status"] in {"PASSED", "DECLARED_ABSENT"}, "Old base build is not green")
    evidence = {"version": 1, "change_id": change["change_id"], "status": "PASSED",
                "contract_digest": change["approval"]["contract_digest"],
                "contract_commit": state["contract_commit"], "source_head": source,
                "baseline_version": baseline["baseline_version"],
                "commands_sha256": change["approval"]["base_commands_sha256"],
                "engine_version": change["approval"]["versions"]["engine_version"],
                "engine_source": state.get("engine_source", str(Path(__file__).resolve().parent)),
                "target_tree_oid": state.get("target_tree_oid") or
                _git(project, "rev-parse", f"{source}^{{tree}}").strip(),
                "layers": layers, "system_smoke": smoke, "build": build}
    path = project / ".storyloop/evidence" / change["change_id"] / "base-old-green.json"
    write_json(path, evidence, max_bytes=4_000_000)
    return evidence


def base_candidate_paths(project: Path, change: dict, state: dict) -> list[str]:
    """Validate every committed human path against the approved base contract."""
    project = Path(project).resolve()
    require(_git(project, "branch", "--show-current").strip() == state["feature_branch"],
            "Resume the saved StoryLoop base branch")
    require(not _git(project, "status", "--porcelain=v1", "--untracked-files=all").strip(),
            "Commit or discard all workspace changes before resuming the base run")
    start = state["contract_commit"]
    require(start in _git(project, "rev-list", "HEAD").splitlines(),
            "Base candidate does not descend from the locked contract commit")
    actual = _changed_since(project, start)
    expected = set(change["product"]["base"]["paths"])
    extra, missing = sorted(actual - expected), sorted(expected - actual)
    require(not extra and not missing,
            "Base committed paths differ from approved contract; extra: " + ", ".join(extra)
            + "; missing: " + ", ".join(missing))
    audit_base_scope(project, change, actual)
    return sorted(actual)



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
        lifecycle = load_json(receipt_path)
        if lifecycle.get("version") == 2:
            require(lifecycle.get("proposal_id") == proposal_id
                    and lifecycle.get("status") == "ACCEPTED",
                    "Base lifecycle receipt is unfinished; resume storyloop run")
            baseline = load_baseline(project)
            require(baseline["source_head"] == lifecycle["integration_commit"]
                    and baseline["baseline_version"] == lifecycle["new_baseline_version"]
                    and _dict_digest(baseline) == lifecycle["candidate_baseline_digest"],
                    "Base lifecycle receipt does not match current baseline")
            return _upgrade_result(project, {**lifecycle,
                                             "source_head": lifecycle["integration_commit"]})
        return _recover_upgrade(project, proposal_path, proposal, receipt_path)
    require(proposal["status"] == "PROPOSED", "Base proposal is already handled")
    categories = {item["category"] for item in proposal["changes"]}
    paths = [item["path"] for item in proposal["changes"]]
    scope = ("ci" if "CI_CHANGE" in categories else
             "upstream" if "UPSTREAM_MUTABLE" in categories else
             "skills" if all(base_scope_path_allowed("skills", path) for path in paths) else
             "knowledge" if all(base_scope_path_allowed("knowledge", path) for path in paths) else
             "foundation")
    state_path = project / ".storyloop/state/changes" / f"{proposal_id}.json"
    if state_path.exists():
        scope = load_change(project, proposal_id)["product"]["base"]["scope"]
    draft_from_proposal(project, proposal_id, scope=scope)
    return {"status": "APPROVAL_REQUIRED", "change_id": proposal_id,
            "next": f"storyloop approve <project> --change {proposal_id}; "
                    f"storyloop run <project> --change {proposal_id} --implementer human"}


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
    approved_ci = {item["path"] for item in proposal["changes"] if item["category"] == "CI_CHANGE"}
    require(all(path in approved_ci for path in actual if is_ci_path(path)),
            "StoryLoop base upgrades never modify CI configuration unless the proposal allowed it")


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
    from .onboarding_manifest import validate_onboarding
    profile = dict(profile)
    validate_onboarding(project, profile)
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
    from .oracle_skill_approval import require_confirmation
    require_confirmation(project, proposal, receipt)
    if receipt.get("oracle_skills_approval") and load_json(receipt_path).get("oracle_skills_approval") != receipt["oracle_skills_approval"]:
        write_json(receipt_path, receipt, max_bytes=4_000_000)
    if receipt["status"] == "ACCEPTED":
        require(baseline["source_head"] == receipt["source_head"]
                and baseline["baseline_version"] == receipt["new_baseline_version"],
                "Accepted base upgrade is not reflected by the current baseline")
        if proposal["status"] != "ACCEPTED":
            proposal["status"] = "ACCEPTED"
            proposal["regression"] = receipt["regression"]
            write_json(proposal_path, proposal, max_bytes=4_000_000)
        return _upgrade_result(project, receipt)
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
    return _upgrade_result(project, receipt)


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
    approved_ci = set(receipt.get("approved_ci_paths") or [])
    require(all(path in approved_ci for path in actual if is_ci_path(path)),
            "StoryLoop base upgrades never modify CI configuration unless the proposal allowed it")


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
        "approved_ci_paths": sorted(item["path"] for item in proposal["changes"] if item["category"] == "CI_CHANGE"),
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


def _lessons_to_review(project: Path, receipt: dict) -> list[str]:
    entries = load_lessons_at(project, receipt["source_head"])
    paths = receipt.get("approved_paths") or []
    return sorted(entry["id"] for entry in entries if entry["status"] == "active"
                  and any(scope_matches(entry["scope"], path) for path in paths))


def _upgrade_result(project: Path, receipt: dict) -> dict:
    result = {"status": "BASELINE_UPGRADED", "proposal_id": receipt["proposal_id"],
              "baseline_version": receipt["new_baseline_version"],
              "source_head": receipt["source_head"]}
    try:
        result["lessons_to_review"] = _lessons_to_review(project, receipt)
    except Exception as exc:
        result["lessons_to_review"] = None
        result["lessons_to_review_error"] = "经验复核列表不可计算：" + str(exc)
    return result


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
