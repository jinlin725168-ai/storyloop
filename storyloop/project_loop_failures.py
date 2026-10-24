"""Record and route failed attempts to the owner of the underlying problem."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .evidence_locations import evidence_location_exists
from .failure_attribution import check_findings, failure_signature, finding_key, no_progress, route
from .models import require, text
from .project_loop_support import save_run
from .run_transitions import transition
from .run_stops import pause
from .smoke import suite_relative

ROUTED_STOPS = {"oracle": "ORACLE_DEFECT", "contract": "NEEDS_SPEC_FIX"}


def epoch_of(state: dict) -> int:
    return len(state.get("repairs") or [])


def _failure(state: dict, entry: dict, attribution: str) -> dict:
    item = {**entry, "attribution": attribution, "epoch": epoch_of(state), "mode": state.get("mode")}
    item["signature"] = failure_signature(item)
    return item


def record_failure(project: Path, state: dict, entry: dict, attribution: str) -> dict | None:
    """Append one judged failure and stop immediately when root-cause mode made no progress."""
    state["failures"].append(_failure(state, entry, attribution))
    save_run(project, state)
    if no_progress(state["failures"], epoch_of(state)):
        return pause(project, state, "NO_PROGRESS",
                     error="根因调查后失败特征仍与上一轮相同，且没有原先失败的用例转绿")
    return None


def record_boundary(project: Path, state: dict, boundary: dict) -> dict:
    """Record a boundary verdict once before preserving the existing boundary stop."""
    attribution = "base" if any(
        item.get("category") == "FOUNDATION_CHANGE" for item in boundary.get("violations") or []) else "implement"
    entry = {"attempt": state["attempt"], "boundary": boundary}
    stopped = record_failure(project, state, entry, attribution)
    if stopped is not None:
        return stopped
    return pause(project, state, boundary["status"], violations=boundary["violations"],
                 base_proposal=boundary["base_proposal"],
                 ci_requires_human_proposal=boundary.get("ci_requires_human_proposal", False))


def oracle_paths(project: Path, state: dict) -> list[str]:
    locked = (state.get("characterization") or {}).get("tests") or []
    return [suite_relative(project, state["change_id"]), *locked]


def _checked_findings(project: Path, change: dict, state: dict, review: dict) -> list[dict]:
    checked = []
    for finding in review.get("findings") or []:
        exists = lambda _path, item=finding: evidence_location_exists(
            project, item.get("location") if isinstance(item, dict) else None, state.get("contract_commit"))
        checked.extend(check_findings([finding], change, oracle_paths(project, state), exists,
                                      set(state.get("overruled") or [])))
    return checked


def route_review(project: Path, change: dict, state: dict, review: dict,
                 layers: dict | None = None) -> dict | None:
    """Attribute checked findings without discarding the deterministic layer evidence."""
    findings = _checked_findings(project, change, state, review)
    review = {**review, "findings": findings}
    entry = {"attempt": state["attempt"], **(layers or {}), "review": review}
    target = route(findings)
    if review["verdict"] == "needs_input":
        # Preserve this judged red attempt even though clarification takes priority over routing.
        state["failures"].append(_failure(state, entry, "implement" if target == "implementation" else target))
        return pause(project, state, "NEEDS_INPUT", questions=review["questions"])
    if target == "implementation":
        return record_failure(project, state, entry, "implement")
    state["failures"].append(_failure(state, entry, target))
    return _pause_routed(project, state, target, findings)


def _pause_routed(project: Path, state: dict, target: str, findings: list[dict]) -> dict:
    blamed = [item for item in findings if item["target"] == target]
    details = {"routed": {"source": "review", "target": target, "findings": blamed},
               "error": blamed[0].get("message", "")}
    if target == "oracle":
        details["acceptance"] = {"status": "ORACLE_DEFECT", "source": "review", "findings": blamed}
    return pause(project, state, ROUTED_STOPS[target], **details)


def require_routed_choice(state: dict, overrule: str | None) -> None:
    routed = state.get("status") in ROUTED_STOPS.values() and state.get("routed")
    if routed and not overrule:
        require(False, f"{state['status']}：审查判定问题不在实现。修正判据用 storyloop acceptance-repair "
                       "--reason <原因>；合同缺陷用 storyloop change --supersede；"
                       "不同意这个判定用 storyloop run --overrule <理由>")
    require(not overrule or routed, "--overrule 只能用于审查判定的 ORACLE_DEFECT 或 NEEDS_SPEC_FIX")


def apply_overrule(project: Path, state: dict, reason: str) -> dict | None:
    """Rewrite the blamed findings, then route the whole mixed review again."""
    reason = text(reason, "overrule reason")
    if state["routed"].get("source") == "consistency":
        from .consistency import overrule_consistency
        return overrule_consistency(project, state, reason)
    routed = state.pop("routed")
    keys = {finding_key(item) for item in routed["findings"]}
    _record_overrule(state, reason, keys)
    last = state["failures"][-1]
    findings = last["review"]["findings"]
    for item in findings:
        if finding_key(item) in keys and item.get("target") == routed["target"]:
            item.update({"target": "implementation", "attribution_note": "已被人驳回，按实现问题处理"})
    target = route(findings)
    last.update({"attribution": "implement" if target == "implementation" else target,
                 "overruled": True})
    last["signature"] = failure_signature(last)
    _clear_stop(state)
    if target in ROUTED_STOPS:
        return _pause_routed(project, state, target, findings)
    transition(project, state, "IMPLEMENTING", event="overrule")
    save_run(project, state)
    return None


def _record_overrule(state: dict, reason: str, keys: set[str]) -> None:
    state.setdefault("overrules", []).append({
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "status": state["status"],
        "attempt": state["attempt"], "reason": reason, "findings": sorted(keys)})
    state["overruled"] = sorted(set(state.get("overruled") or []) | keys)


def _clear_stop(state: dict) -> None:
    for key in ("acceptance", "error", "questions", "next", "stop"):
        state.pop(key, None)
