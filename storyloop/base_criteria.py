"""Source-commit criteria preservation and human review summary for base candidates."""

from __future__ import annotations

import json
from pathlib import Path

from .baseline import _git
from .models import StoryLoopError
from .project_profile import matches_test_root
from .state_format import is_system_smoke_path

MANIFEST = "acceptance/smoke/manifest.json"


def check_base_criteria(project: Path, change: dict, state: dict,
                        approved: set[str]) -> None:
    """Treat missing source paths as deletions, including renamed test files."""
    before, after = _tracked(project, state["source_head"]), _tracked(project, "HEAD")
    roots = _approved_test_roots(project, change, state, before, approved & (before - after))
    if not any(is_system_smoke_path(path) or matches_test_root(path, roots) for path in approved):
        return
    missing_tests = sorted(path for path in before - after if matches_test_root(path, roots))
    missing_checks, missing_ids = _missing_smoke(project, state, approved, before, after)
    if missing_ids or missing_checks or missing_tests:
        raise StoryLoopError(
            f"Base 候选删除了原有判据：冒烟用例 ID={missing_ids}，"
            f"冒烟检查文件={missing_checks}，测试文件={missing_tests}。"
            "请恢复这些用例/文件并提交修订候选后重新运行 storyloop run；"
            "或撤回本次 base 变更。")


def criteria_review_summary(project: Path, change: dict, state: dict) -> list[str]:
    """Show the original cases and files whose contents need independent human review."""
    source = state["source_head"]
    before, after = _tracked(project, source), _tracked(project, "HEAD")
    approved = set(change["product"]["base"]["paths"])
    roots = _approved_test_roots(project, change, state, before, set())
    changed = approved & set(_git(project, "diff", "--no-renames", "--name-only",
                                  source, "HEAD").splitlines())
    tests = sorted(path for path in changed & before & after if matches_test_root(path, roots))
    checks = sorted(path for path in changed & before & after if is_system_smoke_path(path)
                    and path != MANIFEST)
    cases: list[str] = []
    if MANIFEST in changed and MANIFEST in before & after:
        old = {row["id"]: row for row in _manifest(project, source)["tests"]}
        new = {row["id"]: row for row in _manifest(project, "HEAD")["tests"]}
        cases = sorted(case_id for case_id in old.keys() & new.keys()
                       if old[case_id] != new[case_id])
    return ([f"修改原冒烟用例 ID：{', '.join(cases)}"] if cases else []) + (
        [f"修改原冒烟检查文件：{', '.join(checks)}"] if checks else []) + (
        [f"修改原测试文件：{', '.join(tests)}"] if tests else [])


def _approved_test_roots(project: Path, change: dict, state: dict, before: set[str],
                         deleted: set[str]) -> list[str]:
    roots = change["approval"].get("criteria_test_roots")
    if roots is None:
        for path in (".storyloop/config.json", "acceptance/storyloop-settings.json"):
            if path in before:
                data = json.loads(_git(project, "show", f"{state['source_head']}:{path}"))
                roots = data.get("profile", data).get("test_roots")
                break
    if roots is None:
        if deleted:
            raise StoryLoopError("旧 base 批准没有锁定 test_roots，源提交也没有配置画像；"
                                 "无法证明删除路径不是原测试文件，请撤回本次变更并重新提案批准")
        return []
    if not isinstance(roots, list) or not all(isinstance(root, str) for root in roots):
        raise StoryLoopError("Approved test_roots are invalid")
    return roots


def _missing_smoke(project: Path, state: dict, approved: set[str],
                   before: set[str], after: set[str]) -> tuple[list[str], list[str]]:
    if not any(is_system_smoke_path(path) for path in approved) or MANIFEST not in before:
        return [], []
    original = _manifest(project, state["source_head"])
    candidate = _manifest(project, "HEAD") if MANIFEST in after else None
    old_ids = {row["id"] for row in original["tests"]}
    new_ids = {row["id"] for row in candidate["tests"]} if candidate else set()
    references = {arg.removeprefix("./") for row in original["tests"]
                  for arg in row["command"]}
    missing = sorted(path for path in (before - after) & references if is_system_smoke_path(path))
    if MANIFEST not in after:
        missing.insert(0, MANIFEST)
    return missing, sorted(old_ids - new_ids)


def _tracked(project: Path, revision: str) -> set[str]:
    return set(_git(project, "ls-tree", "-r", "--name-only", "-z", revision).split("\0")) - {""}


def _manifest(project: Path, revision: str) -> dict:
    try:
        raw = json.loads(_git(project, "show", f"{revision}:{MANIFEST}"))
    except json.JSONDecodeError as exc:
        raise StoryLoopError("Base system smoke manifest is invalid JSON") from exc
    if (not isinstance(raw, dict) or not isinstance(raw.get("tests"), list)
            or any(not isinstance(row, dict) or not isinstance(row.get("id"), str)
                   or not isinstance(row.get("command"), list)
                   or any(not isinstance(arg, str) for arg in row["command"])
                   for row in raw["tests"])):
        raise StoryLoopError("Base system smoke manifest has invalid cases")
    return raw
