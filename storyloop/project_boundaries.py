"""Single-repository feature and foundation boundary audit."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
import hashlib

from .change_types import definition_for, type_for_change_id, type_of
from .baseline import _git, load_baseline, load_project_config
from .models import identifier, require
from .project_profile import CI_FILES, CUSTOM_ROOT, matches_test_root
from .state_format import is_system_smoke_path
from .storage import load_json, write_json


def audit_base_scope(project: Path, change: dict, paths: set[str]) -> None:
    """Check committed base paths against its proposal, scope and unsafe targets."""
    scope = change["product"]["base"]["scope"]
    proposal_path = project / ".storyloop/base-proposals" / f"{change['change_id']}.json"
    proposal = load_json(proposal_path)
    categories = {item["path"]: item["category"] for item in proposal["changes"]}
    allowed = {"foundation": {"FOUNDATION_CHANGE", "INTEGRATION_CHANGE"},
               "ci": {"CI_CHANGE", "FOUNDATION_CHANGE", "INTEGRATION_CHANGE"},
               "upstream": {"UPSTREAM_MUTABLE", "FOUNDATION_CHANGE", "INTEGRATION_CHANGE"},
               "skills": {"FOUNDATION_CHANGE"}, "knowledge": {"FOUNDATION_CHANGE"}}[scope]
    profile = load_project_config(project)["profile"]
    require(set(categories) == set(change["product"]["base"]["paths"]),
            "Base proposal paths differ from approved contract")
    for path in paths:
        require(categories.get(path) in allowed and base_scope_path_allowed(scope, path),
                f"Base scope {scope} cannot write {path}")
        require(not (is_system_smoke_path(path) or path.startswith("acceptance/changes/")
                     or matches_test_root(path, profile.get("test_roots") or [])
                     or path == "acceptance/storyloop-settings.json"),
                f"Base oracle path requires E2: {path}")
        require(not _git(project, "ls-tree", "HEAD", "--", path).startswith("120000 "),
                f"Base candidate contains unsafe symlink: {path}")
    if change.get("base_proposal_sha256"):
        require(hashlib.sha256(proposal_path.read_bytes()).hexdigest()
                == change["base_proposal_sha256"], "Base proposal changed after approval")


def base_scope_path_allowed(scope: str, path: str) -> bool:
    if scope == "skills":
        return path.startswith((".claude/skills/", ".codex/skills/"))
    if scope == "knowledge":
        return (path.startswith(("openspec/foundation/", "openspec/lessons/"))
                or path == "openspec/principles.json")
    return True


def role_path_boundary(project: Path, role: str, payload: dict) -> dict:
    """Expose the audit's mutable roots for adapter path guards from the same source."""
    baseline, profile = load_baseline(project), load_project_config(project).get("profile", {})
    change = payload.get("change") or {}
    product = change.get("product", {}) if isinstance(change, dict) else {}
    scope = definition_for(type_of(product)).writable_scope
    writable = []
    if role == "implement":
        if "mutable" in scope:
            writable.extend(f"{root.rstrip('/')}/**" for root in baseline["module_roots"])
    if role in {"implement", "characterize"} and "test_roots" in scope:
        writable.extend(f"{root.rstrip('/')}/**" for root in profile.get("test_roots", []))
    plan = payload.get("design_plan") if isinstance(payload.get("design_plan"), dict) else {}
    forbidden = [*baseline["foundation_paths"], *baseline["protected_paths"],
                 "openspec/lessons/**", f"{CUSTOM_ROOT}/**", ".storyloop/**"]
    forbidden.extend(plan.get("forbidden_paths", []) if isinstance(plan.get("forbidden_paths", []), list) else [])
    return {"writable_paths": sorted(set(writable)), "forbidden_paths": sorted(set(forbidden))}


def audit_project_changes(project: Path, change_id: str, *, nature: str | None = None) -> dict:
    """Classify every Git-visible change against the locked logical baseline."""
    project = Path(project).resolve()
    change_id = identifier(change_id, "change id")
    baseline = load_baseline(project)
    scope = definition_for(nature or type_for_change_id(change_id)).writable_scope
    test_roots = _test_roots(project)
    changed = _changed_paths(project)
    allowed: list[dict] = []
    violations: list[dict] = []
    for relative in changed:
        path = project / relative
        record = {"path": relative, "category": _category(relative, baseline, change_id, test_roots, scope)}
        if path.exists() and (path.is_symlink()
                              or (hasattr(path, "is_junction") and path.is_junction())):
            record["category"] = "UNSAFE_LINK"
            violations.append(record)
        elif record["category"] in {"FEATURE", "TEST", "REPORT"}:
            allowed.append(record)
        else:
            violations.append(record)
    base_changes = [
        item for item in violations
        if item["path"] in baseline["foundation_paths"]
        or item["category"] in {"FOUNDATION_CHANGE", "INTEGRATION_CHANGE", "CI_PROTECTED"}
    ]
    proposal = _write_base_proposal(project, change_id, base_changes, baseline) if base_changes else None
    status = "CLEAN" if not changed else ("ALLOWED" if not violations else
             ("BASE_CHANGE_REQUIRED" if base_changes else "BOUNDARY_VIOLATION"))
    return {
        "status": status, "change_id": change_id, "allowed": allowed,
        "violations": violations, "base_proposal": str(proposal) if proposal else None,
    }


def _changed_paths(project: Path) -> list[str]:
    outputs = [
        _git(project, "diff", "--name-only", "-z", "HEAD"),
        _git(project, "ls-files", "--others", "--exclude-standard", "-z"),
    ]
    result: set[str] = set()
    for raw in [item for output in outputs for item in output.split("\0") if item]:
        normalized = raw.replace("\\", "/")
        pure = PurePosixPath(normalized)
        require(normalized and not pure.is_absolute() and ".." not in pure.parts,
                "Git reported an unsafe changed path")
        result.add(normalized)
    return sorted(result, key=str.casefold)


def _test_roots(project: Path) -> list[str]:
    profile = load_project_config(project).get("profile") or {}
    roots = profile.get("test_roots") or []
    return [root for root in roots if isinstance(root, str)]


def _category(relative: str, baseline: dict, change_id: str, test_roots: list[str] = (),
              scope: tuple[str, ...] = ("mutable", "test_roots")) -> str:
    if _is_ci_path(relative):
        return "CI_PROTECTED"
    if (_inside(relative, f"openspec/changes/{change_id}")
            or _inside(relative, f"acceptance/smoke/{change_id}")
            or _inside(relative, f"acceptance/changes/{change_id}")):
        return "CONTRACT_PROTECTED"
    if _inside(relative, "openspec/lessons"):
        return "LESSON_PROTECTED"
    if _inside(relative, CUSTOM_ROOT):
        return "CUSTOM_PROTECTED"
    if is_system_smoke_path(relative):
        return "SMOKE_PROTECTED"
    if relative in baseline["integration_paths"]:
        return "INTEGRATION_CHANGE"
    if "mutable" in scope and any(_inside(relative, root) for root in baseline["module_roots"]):
        return "FEATURE"
    if "test_roots" in scope and matches_test_root(relative, list(test_roots)):
        return "TEST"  # declared test layout: implement and characterize may write it
    if relative in baseline["foundation_paths"]:
        return "FOUNDATION_CHANGE"
    if _inside(relative, f"reports/storyloop/{change_id}"):
        return "REPORT"
    return "OUT_OF_SCOPE"


def _write_base_proposal(
    project: Path, change_id: str, changes: list[dict], baseline: dict,
    reason: str | None = None,
) -> Path:
    root = project / ".storyloop" / "base-proposals"
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    existing = {path.stem for path in root.glob(f"BASE-{stamp}-*.json")}
    number = next(index for index in range(1, 1000)
                  if f"BASE-{stamp}-{index:03d}" not in existing)
    base_id = f"BASE-{stamp}-{number:03d}"
    path = root / f"{base_id}.json"
    write_json(path, {
        "version": 1, "change_id": base_id, "triggered_by": change_id,
        "baseline_version": baseline["baseline_version"], "status": "PROPOSED",
        "baseline_source_head": baseline["source_head"],
        "source_branch": baseline["source_branch"],
        "changes": changes,
        **({"reason": reason} if reason else {}),
    }, max_bytes=1_000_000)
    return path


def _inside(relative: str, root: str) -> bool:
    normalized = root.strip("/")
    return relative == normalized or relative.startswith(normalized + "/")


def _is_ci_path(relative: str) -> bool:
    return relative.startswith(".github/workflows/") or relative in CI_FILES
