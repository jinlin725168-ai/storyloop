"""Bounded review of an acceptance suite before its first RED proof."""
from __future__ import annotations

from pathlib import Path

from .models import StoryLoopError, require
from .project_contracts import validate_review
from .guarded_calls import read_only_call
from .semantic_repair import answer_with_repair


def enabled(state: dict, config: dict) -> bool:
    """Only a light route may opt out; standard and heavy always review."""
    tier = (state.get("routing") or {}).get("effective_tier")
    return tier != "light" or config.get("oracle_strength_precheck", True)


def assess(project: Path, change: dict, state: dict, services, manifest: dict,
           config: dict) -> dict | None:
    # Older contracts have no approved verification seam to review against.
    if not any("verification_entries" in story for story in change["product"]["stories"]):
        return None
    if not enabled(state, config):
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
               "verification_entries": [entry for story in change["product"]["stories"]
                                        for entry in story.get("verification_entries") or []],
               "checklist": ["每条 AC 的全部前置和分支均执行", "比较完整可观察结果",
                             "不得增加合同外断言", "夹具只使用获准资源"]}
    answer = answer_with_repair(
        lambda request: read_only_call(project, change["change_id"],
                                       lambda: services.review(project, request)),
        context, lambda value: _validate(value))
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


def _validate(value: object) -> None:
    validate_review(value)
    require(all(item.get("disposition") for item in value["findings"]),
            "Oracle strength review needs classified findings")


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
