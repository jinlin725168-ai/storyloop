"""Plan-bound updates to existing regression tests and snapshots."""

from pathlib import Path

from .baseline import _git, load_baseline, load_project_config
from .models import require, text
from .path_security import repository_relative, safe_target
from .processutil import redact
from .project_loop_guards import exists_at
from .project_profile import matches_test_root


def validate_updates(project: Path, change: dict, state: dict, option: dict) -> None:
    """Bind every intended old-test edit to an approved AC and a safe plan path."""
    rows = option.get("regression_updates") or []
    roots = load_project_config(project)["profile"].get("test_roots") or []
    baseline = load_baseline(project)
    protected = set(baseline["protected_paths"]) | set(baseline["foundation_paths"])
    protected.update(baseline["integration_paths"])
    criteria = {criterion["id"] for story in change["product"]["stories"]
                for criterion in story["acceptance"]}
    seen = set()
    for row in rows:
        path = repository_relative(row["path"], "regression update path")
        require(path not in seen, f"Duplicate regression update: {path}")
        seen.add(path)
        safe_target(project, path, "regression update")
        require(matches_test_root(path, roots) and path not in protected
                and not path.startswith("acceptance/")
                and exists_at(project, state["contract_commit"], path)
                and _git(project, "ls-tree", state["contract_commit"], "--", path).startswith("100"),
                f"Regression update must name an existing unprotected test file: {path}")
        require(path in option["touched"], f"Regression update is outside touched: {path}")
        ids = row["criterion_ids"]
        require(bool(ids) and len(ids) == len(set(ids)) and set(ids) <= criteria,
                f"Regression update needs valid criterion_ids: {path}")
        text(row["reason"], "regression update reason")
        row["path"] = path
    option["regression_updates"] = rows


def undeclared_existing_tests(project: Path, state: dict, config: dict,
                              boundary: dict, option: dict) -> list[str]:
    """Preserve historical plans; new normalized plans enforce exact old-test paths."""
    if "regression_updates" not in option:
        return []
    declared = {row["path"] for row in option["regression_updates"]}
    roots = config["profile"].get("test_roots") or []
    return sorted(item["path"] for item in boundary["allowed"]
                  if matches_test_root(item["path"], roots)
                  and exists_at(project, state["contract_commit"], item["path"])
                  and item["path"] not in declared)


def mark_undeclared_existing_tests(project: Path, state: dict, config: dict,
                                   boundary: dict, option: dict) -> dict:
    """Convert undeclared old-test edits into ordinary boundary violations."""
    undeclared = undeclared_existing_tests(project, state, config, boundary, option)
    if not undeclared:
        return boundary
    blocked = set(undeclared)
    return {**boundary, "status": "BOUNDARY_VIOLATION",
            "allowed": [item for item in boundary["allowed"] if item["path"] not in blocked],
            "violations": [*boundary["violations"], *(
                {"path": path, "category": "UNDECLARED_REGRESSION_UPDATE"}
                for path in undeclared)]}


def review_diffs(project: Path, state: dict, option: dict) -> list[dict]:
    """Show the reviewer only declared old-test diffs, bounded and redacted."""
    return [{"path": row["path"], "criterion_ids": row["criterion_ids"],
             "reason": row["reason"], "diff": redact(_git(
                 project, "diff", "--no-ext-diff", "--no-textconv",
                 state["contract_commit"], "--", row["path"]))[:12_000]}
            for row in option.get("regression_updates") or []]
