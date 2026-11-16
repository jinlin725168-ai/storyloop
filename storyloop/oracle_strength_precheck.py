"""Bounded review of an acceptance suite before its first RED proof."""
from __future__ import annotations

from pathlib import Path
import re

from .models import StoryLoopError, require
from .project_contracts import validate_review
from .guarded_calls import read_only_call
from .semantic_repair import answer_with_repair


def enabled(state: dict, config: dict, change: dict | None = None) -> bool:
    """Review suites where a weak oracle is costly: heavy routes, contracts that change existing
    behaviour, and stateful changes. Below heavy, a purely additive contract skips it (its RED
    proof and final review remain); a light route may also opt out by configuration."""
    tier = (state.get("routing") or {}).get("effective_tier")
    if tier == "light" and not config.get("oracle_strength_precheck", True):
        return False
    if tier != "heavy" and change is not None and _additive_without_state(change["product"]):
        return False
    return True


def _additive_without_state(product: dict) -> bool:
    from .intake import contract_change

    risks = (product.get("assessment") or {}).get("risks") or {}
    stateful = any((risks.get(key) or {}).get("value") != "no"
                   for key in ("persistent_state", "migration"))
    return not stateful and contract_change(product) == "safe"


def assess(project: Path, change: dict, state: dict, services, manifest: dict,
           config: dict) -> dict | None:
    # Older contracts have no approved verification seam to review against.
    if not any("verification_entries" in story for story in change["product"]["stories"]):
        return None
    if not enabled(state, config, change):
        return None
    from .smoke import smoke_digests, suite_root

    digests = smoke_digests(project, change)
    previous = state.get("oracle_strength_precheck") or {}
    if previous.get("verdict") == "pass" and previous.get("suite_digests") == digests:
        return previous

    root = suite_root(project, change["change_id"])
    files = [{"path": path.relative_to(root).as_posix(), "content": path.read_text(encoding="utf-8")}
             for path in sorted(root.rglob("*")) if path.is_file() and path.name != "manifest.json"]
    context = {"mode": "oracle-strength", "change": change, "manifest": manifest,
               "suite_files": files, "criteria": [criterion for story in change["product"]["stories"]
                                                  for criterion in story["acceptance"]],
               "previous_findings": ((state.get("acceptance") or {}).get("precheck") or {}).get("findings") or [],
               "answers": _answers(project, change["change_id"]),
               "verification_entries": [entry for story in change["product"]["stories"]
                                        for entry in story.get("verification_entries") or []],
               "checklist": ["每条 AC 的全部前置和分支均执行", "比较完整可观察结果",
                             "不得增加合同外断言", "夹具只使用获准资源"]}
    context["precheck_round"] = 1 + sum(item.get("automatic") is True for item in
                                        state.get("repairs") or [])
    if context["precheck_round"] >= 2:
        context["prior_suite_edits"] = _prior_suite_edits(project, state)
    if context["precheck_round"] >= 3:
        context["required_spec_fields"] = ["inputs", "preconditions", "entry", "expected",
                                           "negative_boundary", "contract_plan"]
    answer = answer_with_repair(
        lambda request: read_only_call(project, change["change_id"],
                                       lambda: services.review(project, request)),
        context, lambda value: _validate(value, context))
    _downgrade_late_findings(answer, context)
    verdict = validate_review(answer)
    result = {"verdict": verdict, "summary": answer["summary"],
              "findings": answer["findings"], "questions": answer["questions"],
              "suite_digests": digests}
    if verdict == "fail":
        raise StoryLoopError("Oracle strength precheck found weak acceptance assertions",
                             code="ORACLE_DEFECT", details={"fixture_repairable": True,
                             "defect_category": "ORACLE_STRENGTH_PRECHECK", "precheck": result})
    if verdict == "needs_input":
        raise StoryLoopError("Oracle strength precheck needs a human decision",
                             code="NEEDS_INPUT", details={"precheck": result})
    return result


def _answers(project: Path, change_id: str) -> list[dict]:
    """Human answers to earlier pauses, so a settled decision is not asked again."""
    from .answers import load_answers
    try:
        return load_answers(project, change_id)
    except (StoryLoopError, OSError):
        return []


def _prior_suite_edits(project: Path, state: dict) -> list[str]:
    """Read the last repair commit without changing the persisted run-state format."""
    repair = (state.get("repairs") or [{}])[-1]
    if repair.get("changed_paths") is not None:  # accepts older test adapters
        return repair["changed_paths"]
    commit = repair.get("commit")
    if not commit:
        return []
    from .baseline import _git
    try:
        return _git(project, "diff-tree", "--no-commit-id", "--name-only", "-r", commit).splitlines()
    except (StoryLoopError, OSError):
        return []


def _validate(value: object, context: dict | None = None) -> None:
    validate_review(value)
    require(all(item.get("disposition") for item in value["findings"]),
            "Oracle strength review needs classified findings")
    context = context or {}
    entries = {criterion["id"]: set(criterion.get("entry_ids") or []) for criterion in
               context.get("criteria") or []}
    from .review_convergence import _missing_spec
    for item in value["findings"]:
        if item.get("disposition") != "MUST_FIX":
            continue
        change = item.get("required_change")
        require(isinstance(change, dict) and all(isinstance(change.get(key), str)
                and change[key].strip() for key in ("input", "assertion", "expected")),
                f"{item.get('id')} required_change needs input, assertion and expected")
        if context.get("precheck_round", 1) < 3 or _late(item, context):
            continue  # a late finding is downgraded, not sent back for a fuller spec
        missing = _missing_spec(item, entries)
        require(not missing, f"{item['id']} criterion_spec needs {', '.join(missing)}")


def _late(item: dict, context: dict) -> bool:
    """A blocking finding first raised after a repair that it cannot attribute to that repair.

    The review must report every blocking gap in its first pass; a later one blocks only when
    introduced_by names a file the previous repair changed (2026-11-16 plan B3).
    """
    if context.get("precheck_round", 1) < 2:
        return False
    prior = {finding.get("id") for finding in context.get("previous_findings") or []}
    if item.get("id") in prior:
        return False
    # "path:line reason"; any punctuation (including full-width "：，") may separate the reason.
    match = re.search(r"([^\s:：]+):(\d+)[\s:：,，;；。.\-—]*(\S.*)", item.get("introduced_by") or "")
    if match is None:
        return True
    location = match.group(1)
    edits = context.get("prior_suite_edits") or []
    return not any(path == location or path.endswith("/" + location) for path in edits)


def _downgrade_late_findings(answer: dict, context: dict) -> None:
    for item in answer["findings"]:
        if item.get("disposition") != "MUST_FIX" or not _late(item, context):
            continue
        change = item.get("required_change")
        if isinstance(change, dict):
            change = "；".join(f"{key}: {change[key]}" for key in ("input", "assertion", "expected")
                              if change.get(key))
        item.update(disposition="ADVISORY", blocking=False, downgraded="late_finding",
                    required_change=change or "见 message")


def stamp_red(project: Path, change_id: str, precheck: dict) -> dict:
    """Bind the review to the exact RED evidence that follows it."""
    from .smoke import RED_EVIDENCE
    from .storage import load_json, write_json
    from .run_guards import read_red_evidence

    path = project / ".storyloop/evidence" / change_id / RED_EVIDENCE
    evidence = load_json(path)
    evidence["oracle_strength_precheck"] = precheck
    write_json(path, evidence, max_bytes=4_000_000)
    return read_red_evidence(project, change_id)
