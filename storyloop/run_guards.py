"""Deterministic lifecycle checks; G1–G3 move here in Tasks 4 and 5."""
import hashlib
from pathlib import Path
from .change_types import definition_for, type_for_change_id, type_of
from .models import require
from .smoke import RED_EVIDENCE
from .storage import load_json
from .baseline import _git, load_baseline, load_project_config
from .oracle_counts import check_layers
from .project_boundaries import audit_project_changes
from .project_git import current_branch
from .project_profile import base_commands_digest, layer_commands
from .project_receipt import verify_verified_artifacts, recover_completed_merge  # noqa: F401  # 兼容再导出：保留旧模块导入路径
from .smoke import RESULT_EVIDENCE, smoke_digests
from .smoke_runner import candidate_digest, implementation_digest


def guard_updates(value: dict) -> None:
    """A caller or delivery effect cannot smuggle a second status assignment."""
    require(isinstance(value, dict) and "status" not in value,
            "Transition updates must be a dictionary without status")


def read_red_evidence(project: Path, change_id: str) -> dict:
    """Preserve the existing cache check: identity/status only, not new fingerprint policy."""
    path = project / ".storyloop/evidence" / change_id / RED_EVIDENCE
    require(path.is_file(), "RED evidence is missing")
    value = load_json(path)
    require(value.get("change_id") == change_id and value.get("status") == "RED",
            "Stored RED evidence is invalid")
    return {**value, "evidence_path": str(path),
            "evidence_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def guard_g1(project: Path, state: dict, context: dict) -> None:
    """Fresh RED is judged by the controlled runner; cached RED keeps its old weak check."""
    require(bool(state.get("contract_commit")), "Contract must be committed before implementation")
    floor = definition_for(type_for_change_id(state["change_id"])).criteria_floor
    if "full_regression_green" in floor:
        _guard_base_green(project, state)
        return
    require(bool(set(floor) & {"acceptance_red", "defect_reproduction_red", "structural_red"}),
            "Change type lacks a supported RED floor")
    read_red_evidence(project, state["change_id"])


def _guard_base_green(project: Path, state: dict) -> None:
    path = project / ".storyloop/evidence" / state["change_id"] / "base-old-green.json"
    require(path.is_file(), "Old-base green evidence is missing")
    evidence = load_json(path)
    baseline = load_baseline(project)
    require(evidence.get("change_id") == state["change_id"]
            and evidence.get("status") == "PASSED"
            and evidence.get("contract_digest") == state.get("contract_digest")
            and evidence.get("contract_commit") == state["contract_commit"]
            and evidence.get("source_head") == state.get("source_head", baseline["source_head"])
            and evidence.get("baseline_version") == baseline["baseline_version"],
            "Old-base green evidence does not match the approved run")
    require(evidence.get("system_smoke", {}).get("status") == "PASSED"
            and evidence.get("build", {}).get("status") in {"PASSED", "DECLARED_ABSENT"},
            "Old-base green evidence has missing checks")
    layers = evidence.get("layers") or {}
    require(set(layers) == {"unit", "integration", "checks"}
            and all(row.get("status") in {"PASSED", "SKIPPED"} for row in layers.values())
            and any(row.get("status") == "PASSED" for row in layers.values()),
            "Old-base regression evidence is incomplete")


def manual_criteria(change: dict) -> list[str]:
    if type_of(change["product"]) == "base":
        return ["BASE-ACCEPT"]
    return [criterion["id"] for story in change["product"]["stories"]
            for criterion in story["acceptance"] if criterion["kind"] == "manual"]



def pending_manual_criteria(change: dict, records: list[dict], candidate_sha256: str) -> list[str]:
    """Manual criteria without an acceptance bound to exactly this Git-visible code state."""
    accepted = {record["criterion_id"] for record in records
                if record.get("candidate_sha256") == candidate_sha256}
    return [criterion for criterion in manual_criteria(change) if criterion not in accepted]



def _engine_output_roots(change_id: str) -> tuple[str, ...]:
    return (f"openspec/changes/{change_id}/design.md", f"openspec/changes/{change_id}/decisions.md",
            "openspec/specs/", "acceptance/ui/", f"reports/storyloop/{change_id}/")



def _without_engine_outputs(boundary: dict, change_id: str) -> dict:
    roots = _engine_output_roots(change_id)
    keep = lambda item: not any(item["path"] == root or item["path"].startswith(root) for root in roots)  # noqa: E731
    allowed = [i for i in boundary["allowed"] if keep(i)]
    violations = [i for i in boundary["violations"] if keep(i)]
    status = ("CLEAN" if not allowed and not violations else "ALLOWED" if not violations
              else boundary["status"])
    return {**boundary, "allowed": allowed, "violations": violations, "status": status,
            "base_proposal": boundary.get("base_proposal") if violations else None}



def _implementation_already_committed(project: Path, subject: str, boundary: dict) -> str | None:
    """HEAD is the implementation commit of this change and nothing is left to commit."""
    if boundary["allowed"] or boundary["violations"]:
        return None
    if _git(project, "log", "-1", "--format=%s").strip() != subject:
        return None
    if _git(project, "status", "--porcelain=v1", "--untracked-files=all").strip():
        return None
    return _git(project, "rev-parse", "HEAD").strip()



LAYER_ORDER = ("unit", "integration", "acceptance", "smoke", "checks")


def layers_green(layers: dict) -> bool:
    """Acceptance passes; absent optional commands retain their historical SKIPPED result."""
    return (layers["acceptance"]["status"] == "PASSED"
            and all(layers[name]["status"] in {"PASSED", "SKIPPED"}
                    for name in LAYER_ORDER if name != "acceptance"))


def _manual_records(project: Path, change_id: str) -> list[dict]:
    path = project / ".storyloop/state/acceptance" / f"{change_id}.json"
    if not path.exists():
        return []
    value = load_json(path)
    require(value.get("version") == 1 and value.get("change_id") == change_id
            and isinstance(value.get("records"), list), "Invalid manual acceptance file")
    return value["records"]


def guard_g2(project: Path, change: dict, state: dict, context: dict) -> None:
    """Read authoritative evidence before any implementation/report delivery commit."""
    if type_of(change["product"]) == "base":
        _guard_base_g2(project, change, state)
        return
    layers, review = context.get("layers"), context.get("review") or {}
    require(isinstance(layers, dict) and set(layers) == set(LAYER_ORDER),
            "Report needs every verification layer")
    checked = check_layers(layers, state.get("oracle"))
    require(layers_green(checked), "Verification layers did not pass")
    profile = load_project_config(project)["profile"]
    for name in ("unit", "integration", "checks"):
        require(not layer_commands(profile, name) or layers[name]["status"] == "PASSED",
                f"Configured {name} layer cannot be skipped")
    require(review.get("verdict") == "pass" and not review.get("findings") and not review.get("questions"),
            "Independent review did not pass")
    identifier = change["change_id"]
    boundary = _without_engine_outputs(context.get("boundary") or {}, identifier)
    final = _without_engine_outputs(audit_project_changes(
        project, identifier, nature=type_of(change["product"])), identifier)
    subject = f"feat({identifier}): implement approved product change"
    already = _implementation_already_committed(project, subject, final)
    require(not boundary["violations"] and (already or final == boundary) and not final["violations"],
            "Workspace changed after final boundary audit")
    require(already or bool(final["allowed"]), "Passing implementation produced no auditable code changes")
    digest = implementation_digest(project)
    require(not pending_manual_criteria(change, _manual_records(project, identifier), digest),
            "Manual acceptance is missing for the code being reported")
    _acceptance_binding(project, change, layers["acceptance"])


def _guard_base_g2(project: Path, change: dict, state: dict) -> None:
    change_id = change["change_id"]
    evidence_path = project / ".storyloop/evidence" / change_id / "base-candidate-green.json"
    evidence = load_json(evidence_path)
    digest = implementation_digest(project)
    require(evidence.get("status") == "PASSED"
            and evidence.get("candidate_commit") == state.get("candidate_commit")
            and evidence.get("candidate_sha256") == digest
            and evidence.get("contract_digest") == state.get("contract_digest"),
            "Base verification evidence is stale")
    require(evidence.get("system_smoke", {}).get("status") == "PASSED"
            and evidence.get("build", {}).get("status") in {"PASSED", "DECLARED_ABSENT"},
            "Base smoke or build did not pass")
    profile = load_project_config(project)["profile"]
    require(base_commands_digest(profile) == change["approval"]["base_commands_sha256"],
            "Approved base commands changed before G2")
    layers = evidence.get("layers") or {}
    require(set(layers) == {"unit", "integration", "checks"}, "Base regression layers are incomplete")
    for name in layers:
        require(not layer_commands(profile, name) or layers[name]["status"] == "PASSED",
                f"Base {name} regression did not pass")
    review = load_json(project / ".storyloop/state/base-reviews" / f"{change_id}.json")
    require(review.get("verdict") == "pass" and review.get("candidate_sha256") == digest
            and review.get("candidate_commit") == state.get("candidate_commit")
            and review.get("reviewer") != _git(project, "show", "-s", "--format=%an",
                                              state["candidate_commit"]).strip(),
            "Independent base review is missing")
    require(not pending_manual_criteria(change, _manual_records(project, change_id), digest),
            "Base manual acceptance is missing")
    actual = {item for item in _git(project, "diff", "--name-only", "-z",
                                    state["contract_commit"], "HEAD").split("\0") if item}
    require(actual == set(change["product"]["base"]["paths"]),
            "Base committed paths differ from the approved contract")


def _acceptance_binding(project: Path, change: dict, smoke: dict) -> None:
    change_id = change["change_id"]
    path = project / ".storyloop/evidence" / change_id / RESULT_EVIDENCE
    evidence = load_json(path)
    supplied = {key: value for key, value in smoke.items()
                if key not in {"evidence_path", "evidence_sha256", "uncounted", "oracle_problems"}}
    require(smoke.get("evidence_sha256") == hashlib.sha256(path.read_bytes()).hexdigest()
            and supplied == evidence, "Smoke evidence does not match the protected evidence file")
    require(evidence.get("suite_digests") == smoke_digests(project, change),
            "Smoke evidence is not bound to the protected test bytes")
    require(evidence.get("workspace_sha256") == candidate_digest(project),
            "Smoke evidence is not bound to the implementation workspace")


def guard_g3(project: Path, state: dict, context: dict) -> None:
    require(not _git(project, "status", "--porcelain=v1", "--untracked-files=all").strip(),
            "Verified feature worktree must remain clean")
    require(current_branch(project) == state["feature_branch"],
            "Verified feature branch is not checked out")
    require(_git(project, "rev-parse", state["feature_branch"]).strip() == state["report_commit"],
            "Verified feature branch changed after reporting")
    require(_git(project, "rev-parse", state["source_branch"]).strip() == state["source_head"],
            "Source branch changed after the feature run started")
    verify_verified_artifacts(project, state)


def reset_status(state: dict) -> str:
    """Keep the legacy interrupted-status selector, including old nonstandard values."""
    if state["status"] == "MANUAL_ACCEPTANCE_REQUIRED":
        return "IMPLEMENTING"
    if state.get("planning", {}).get("origin") == "L3" and state.get("phase") == "planning":
        return "DESIGN_REVISION_REQUIRED"
    if state.get("interrupted_status"):
        return state["interrupted_status"]
    if int(state.get("attempt", 0)) == 0:
        return "CONTRACT_COMMITTED" if state.get("contract_commit") else "PREPARING"
    return "IMPLEMENTING"


def guard_cost_restore(project: Path, state: dict) -> None:
    require(state.get("cost_resume_digest") == implementation_digest(project),
            "Workspace changed while cost stop was paused; restore the saved candidate before run")
