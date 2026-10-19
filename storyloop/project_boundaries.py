"""Single-repository feature and foundation boundary audit."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from .baseline import _git, load_baseline, load_project_config
from .models import identifier, require
from .project_profile import CI_FILES, matches_test_root
from .system_smoke import is_system_smoke_path
from .storage import write_json


def role_path_boundary(project: Path, role: str, payload: dict) -> dict:
    """Expose the audit's mutable roots for adapter path guards from the same source."""
    baseline, profile = load_baseline(project), load_project_config(project).get("profile", {})
    writable = []
    if role == "implement":
        writable = [*(f"{root.rstrip('/')}/**" for root in baseline["module_roots"]),
                    *(f"{root.rstrip('/')}/**" for root in profile.get("test_roots", []))]
    elif role == "characterize":
        writable = [f"{root.rstrip('/')}/**" for root in profile.get("test_roots", [])]
    plan = payload.get("design_plan") if isinstance(payload.get("design_plan"), dict) else {}
    forbidden = [*baseline["foundation_paths"], *baseline["protected_paths"], ".storyloop/**"]
    forbidden.extend(plan.get("forbidden_paths", []) if isinstance(plan.get("forbidden_paths", []), list) else [])
    return {"writable_paths": sorted(set(writable)), "forbidden_paths": sorted(set(forbidden))}


def audit_project_changes(project: Path, change_id: str) -> dict:
    """Classify every Git-visible change against the locked logical baseline."""
    project = Path(project).resolve()
    change_id = identifier(change_id, "change id")
    baseline = load_baseline(project)
    test_roots = _test_roots(project)
    changed = _changed_paths(project)
    allowed: list[dict] = []
    violations: list[dict] = []
    for relative in changed:
        path = project / relative
        record = {"path": relative, "category": _category(relative, baseline, change_id, test_roots)}
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


def _category(relative: str, baseline: dict, change_id: str, test_roots: list[str] = ()) -> str:
    if _is_ci_path(relative):
        return "CI_PROTECTED"
    if (_inside(relative, f"openspec/changes/{change_id}")
            or _inside(relative, f"acceptance/smoke/{change_id}")
            or _inside(relative, f"acceptance/changes/{change_id}")):
        return "CONTRACT_PROTECTED"
    if is_system_smoke_path(relative):
        return "SMOKE_PROTECTED"
    if relative in baseline["integration_paths"]:
        return "INTEGRATION_CHANGE"
    if any(_inside(relative, root) for root in baseline["module_roots"]):
        return "FEATURE"
    if matches_test_root(relative, list(test_roots)):
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
