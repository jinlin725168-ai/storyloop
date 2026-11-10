"""Record and route failed attempts to the owner of the underlying problem."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import hashlib

from .evidence_locations import evidence_location_exists
from .base_collision import link_base_collision
from .failure_attribution import (check_findings, failure_signature, finding_key, location_file,
                                  no_progress, route, contract_ids)
from .models import require, text
from .project_loop_support import save_run
from .run_transitions import transition
from .run_stops import pause
from .smoke import suite_relative
from .baseline import _git
from .path_security import safe_target
from .boundaries import capture_snapshot
from .project_loop_guards import exists_at
from .project_profile import matches_test_root
from .processutil import redact

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


def record_boundary(project: Path, state: dict, boundary: dict,
                    touched: list | None = None, preexisting: set[str] | None = None) -> dict:
    """Record a boundary verdict once before preserving the existing boundary stop."""
    linked_base = None
    if touched is not None and state.get("e3_collision") is True:
        linked_base = link_base_collision(project, state, boundary, touched, preexisting or set())
    attribution = "base" if any(
        item.get("category") == "FOUNDATION_CHANGE" for item in boundary.get("violations") or []) else "implement"
    entry = {"attempt": state["attempt"], "boundary": boundary}
    stopped = record_failure(project, state, entry, attribution)
    if stopped is not None:
        return stopped
    details = {"violations": boundary["violations"], "base_proposal": boundary["base_proposal"]}
    state.pop("legacy_paths", None)
    if linked_base:
        legacy_paths = sorted({item["path"] for item in boundary["allowed"]
                               if item["category"] in {"FEATURE", "TEST"}
                               and item["path"] in (preexisting or set())})
        if legacy_paths:
            details["legacy_paths"] = legacy_paths
    if state.get("blocked_by"):
        details["blocked_by"] = state["blocked_by"]
    if boundary.get("ci_requires_human_proposal"):
        details["ci_requires_human_proposal"] = True
    return pause(project, state, boundary["status"], **details)


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
                 layers: dict | None = None, config: dict | None = None) -> dict | None:
    """Attribute checked findings without discarding the deterministic layer evidence."""
    if config is not None and review["verdict"] == "fail":
        conflict = reviewed_regression_conflict(project, change, state, review, layers or {}, config)
        if conflict is not None:
            return conflict
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
    if target == 'oracle' and reviewed_characterization_conflict(project, change, state, review):
        state['characterization_pause_digest'] = characterization_workspace_digest(project)
    return _pause_routed(project, state, target, findings)


def reviewed_regression_conflict(project: Path, change: dict, state: dict,
                                 review: dict, layers: dict, config: dict) -> dict | None:
    """Route a confirmed old-test conflict to replan before another implement attempt."""
    from .planning import plan_projection
    plan = plan_projection(state) or {}
    if "regression_updates" not in plan or all(
            layer.get("status") in {"PASSED", "SKIPPED"} for layer in layers.values()
            if isinstance(layer, dict)):
        return None
    declared = {row["path"] for row in plan["regression_updates"]}
    roots = config["profile"].get("test_roots") or []
    for finding in _checked_findings(project, change, state, review):
        path = location_file(finding.get("location"))
        confirmed = (finding.get("regression_conflict") is True or
                     finding.get("introduced") is False)
        if (finding.get("perspective") != "oracle-strength" or not confirmed
                or finding.get("disposition") == "ADVISORY"
                or finding.get("criterion_id") not in contract_ids(change)
                or not path or not matches_test_root(path, roots) or path in declared
                or not exists_at(project, state["contract_commit"], path)):
            continue
        summary = _failed_layer_summary(layers)
        conflict = {"path": path, "criterion_id": finding["criterion_id"],
                    "finding_id": finding.get("id"), "failure_summary": summary,
                    "review_evidence": redact(str(finding.get("evidence") or ""))[:1500]}
        state["failures"].append(_failure(state, {"attempt": state["attempt"], **layers,
                                            "review": review}, "design"))
        return pause(project, state, "DESIGN_REVISION_REQUIRED",
                     error="Existing regression assertion conflicts with the approved contract; replan",
                     regression_conflict=conflict)
    return None


def _failed_layer_summary(layers: dict) -> str:
    for name, layer in layers.items():
        if not isinstance(layer, dict) or layer.get("status") in {"PASSED", "SKIPPED"}:
            continue
        for result in layer.get("results") or []:
            if result.get("status") in {"PASS", "NOT_RUN"}:
                continue
            detail = result.get("output_tail") or result.get("stderr") or result.get("stdout") or ""
            return redact(f"{name}/{result.get('id')}: {result.get('status')} {detail}")[-1500:]
    return redact(str(layers.get("unit", {}).get("status") or "review failure"))[:1500]


def reviewed_characterization_conflict(project: Path, change: dict, state: dict,
                                       review: dict) -> bool:
    """Accept only evidenced oracle findings in this run's characterization files."""
    tests = set((state.get('characterization') or {}).get('tests') or [])
    if not tests or not (state.get('characterization') or {}).get('commits'):
        return False
    checked = _checked_findings(project, change, state, review)
    findings = [item for item in checked if item.get('target') == 'oracle']
    return (bool(findings) and not any(item.get('target') == 'contract' for item in checked)
            and all(location_file(item.get('location')) in tests for item in findings))


def characterization_conflict_evidence(project: Path, change: dict, state: dict,
                                       review: dict) -> list[dict]:
    tests = set((state.get('characterization') or {}).get('tests') or [])
    return [item for item in _checked_findings(project, change, state, review)
            if item.get('target') == 'oracle' and location_file(item.get('location')) in tests]


def characterization_workspace_digest(project: Path) -> str:
    """Bind a routed pause to business-file bytes without recording their contents."""
    result = hashlib.sha256()
    for path, fingerprint in capture_snapshot(project).entries:
        result.update(f'{path}\0{fingerprint.sha256}\0{fingerprint.kind}\0{fingerprint.mode}\n'.encode())
    return result.hexdigest()


def discard_characterization_attempt(project: Path, state: dict, boundary: dict,
                                     evidence: list[dict]) -> bool:
    """Drop the reviewed candidate and the entire lock batch, retaining its parent commit."""
    commits = (state.get('characterization') or {}).get('commits') or []
    if not commits or _git(project, 'rev-parse', 'HEAD').strip() != commits[-1]:
        return False
    anchor = _git(project, 'rev-parse', f'{commits[0]}^').strip()
    if any(_git(project, 'rev-list', '--parents', '-n', '1', commit).split()[1:]
           != [anchor if index == 0 else commits[index - 1]]
           for index, commit in enumerate(commits)):
        return False
    changed = [path for path in _git(project, 'diff', '--no-renames', '--name-only', '-z',
                                     anchor).split('\0') if path]
    untracked = [path for path in _git(project, 'ls-files', '--others', '--exclude-standard', '-z')
                 .split('\0') if path]
    allowed = {item['path'] for item in boundary['allowed']}
    allowed.update((state.get('characterization') or {}).get('tests') or [])
    extra = sorted((set(changed) | set(untracked)) - allowed)
    if extra:
        return False
    for path in untracked:
        target = safe_target(project, path, 'discarded candidate')
        if not target.is_file() or target.is_symlink():
            return False
    _git(project, 'reset', '--soft', anchor)
    if changed:
        _git(project, 'restore', '--source', anchor, '--staged', '--worktree', '--', *changed)
    for path in untracked:
        safe_target(project, path, 'discarded candidate').unlink()
    failures = state.get('failures') or []
    if (failures and failures[-1].get('attempt') == state['attempt']
            and failures[-1].get('attribution') == 'oracle'):
        state.setdefault('voided_characterization_reviews', []).append(failures.pop())
    state['characterization_conflict_restarts'] = state.get('characterization_conflict_restarts', 0) + 1
    state['characterization_conflict_evidence'] = evidence
    state.pop('characterization_pause_digest', None)
    state.pop('characterization', None)
    for key in ('pending_review', 'cost_candidate', 'verified', 'candidate_sha256', 'mode',
                'routed', 'acceptance', 'error', 'next', 'stop'):
        state.pop(key, None)
    consumed = (state.get('planning') or {}).get('consumed_attempt', 0)
    state['attempt'] = max(consumed, state['attempt'] - 1)
    save_run(project, state)
    return True


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
        from .failure_attribution import characterization_oracle
        if characterization_oracle(state):
            require(False, f"{state['status']}：特征化测试与合同冲突；请用 storyloop change <project> "
                           f"--supersede {state['change_id']} --reason <原因> "
                           "--message <重新描述需求> 作废本次变更并重开；"
                           "不同意这个判定用 storyloop run --overrule <理由>")
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
    from .intervention_events import append_event
    append_event(state, "overrule", source="run --overrule", stop=state["status"])
    state.setdefault("overrules", []).append({
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "status": state["status"],
        "attempt": state["attempt"], "reason": reason, "findings": sorted(keys)})
    state["overruled"] = sorted(set(state.get("overruled") or []) | keys)


def _clear_stop(state: dict) -> None:
    for key in ("acceptance", "error", "questions", "next", "stop"):
        state.pop(key, None)
