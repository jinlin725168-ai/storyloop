"""Pure cross-change metrics, with one explicit read-only Git metric."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from . import baseline
from .metrics_compute_support import (
    TIER_ORDER,
    UNRECORDED,
    _count_decision,
    _count_integer_fields,
    _count_list_field,
    _counts,
    _forced_summary,
    _gap,
    _has_stop,
    _id,
    _implement_tier,
    _last_signatures,
    _median_result,
    _miss,
    _nonnegative_int,
    _numstat,
    _ratio,
    _supersede_links,
    _unique,
    _upgraded,
    metric,
)
from .models import StoryLoopError

def first_pass(records: list[dict]) -> dict:
    """M1: passed changes without any recorded correction failure."""
    passed, first, missing = [], [], []
    for record in records:
        if record.get("status") != "PASSED":
            if not isinstance(record.get("status"), str):
                _miss(missing, record, _gap(record, "status"))
            continue
        failures = record.get("failures")
        if not isinstance(failures, list):
            _miss(missing, record, _gap(record, "failures"))
            continue
        passed.append(_id(record))
        if not failures:
            first.append(_id(record))
    return metric("M1", len(first), len(passed), first, missing)


def attempts(records: list[dict]) -> dict:
    """M1b: mean attempts plus full-cohort budget exhaustion evidence."""
    valid, total, budget, progressing, progress_n, missing = [], 0, [], [], 0, []
    for record in records:
        value = record.get("attempts")
        if _nonnegative_int(value):
            valid.append(_id(record))
            total += value
        else:
            _miss(missing, record, _gap(record, "attempts"))
        if not _has_stop(record, "BUDGET_EXHAUSTED"):
            continue
        budget.append(_id(record))
        signatures = _last_signatures(record.get("failures"))
        if signatures is None:
            _miss(missing, record, _gap(record, "failures"))
            continue
        progress_n += 1
        if signatures[0] != signatures[1]:
            progressing.append(_id(record))
    result = metric("M1b", total, len(valid), budget, missing)
    result.update({
        "attempt_changes": valid,
        "budget_exhausted": len(budget),
        "budget_denominator": len(records),
        "budget_value": _ratio(len(budget), len(records)),
        "progressing": len(progressing),
        "progressing_denominator": progress_n,
        "progressing_value": _ratio(len(progressing), progress_n),
        "progressing_changes": progressing,
    })
    return result


def upgrades(records: list[dict]) -> dict:
    """M2: changes whose routing tier increased after execution began."""
    eligible: list[tuple[str, str, bool]] = []
    missing: list[dict] = []
    for record in records:
        history, tier = record.get("routing_history"), record.get("tier_initial")
        if not isinstance(history, list):
            _miss(missing, record, "NO_ROUTING")
            continue
        if tier not in TIER_ORDER:
            _miss(missing, record, "NO_TIER_RECORDED")
            continue
        upgraded = _upgraded(history)
        if upgraded is None:
            _miss(missing, record, "NO_ROUTING")
            continue
        eligible.append((_id(record), tier, upgraded))
    changes = [change_id for change_id, _, upgraded in eligible if upgraded]
    by = {
        tier: metric("M2", sum(item[2] for item in rows), len(rows),
                     [item[0] for item in rows if item[2]])
        for tier in TIER_ORDER
        if (rows := [item for item in eligible if item[1] == tier])
    }
    return metric("M2", len(changes), len(eligible), changes, missing, by)


def rework(records: list[dict]) -> dict:
    """M3: contract rework rate and all recorded failure attributions."""
    valid, changed, missing, attribution = [], [], [], Counter()
    for record in records:
        failures, stops = record.get("failures"), record.get("stops")
        if not isinstance(failures, list) and not isinstance(stops, (list, set, tuple)):
            _miss(missing, record, _gap(record, "failures"))
            continue
        valid.append(_id(record))
        known_failures = failures if isinstance(failures, list) else []
        for failure in known_failures:
            name = failure.get("attribution") if isinstance(failure, dict) else None
            attribution[name if isinstance(name, str) and name else UNRECORDED] += 1
        if (isinstance(stops, (list, set, tuple)) and "NEEDS_SPEC_FIX" in stops
                or any(isinstance(item, dict) and item.get("attribution") == "contract"
                       for item in known_failures)):
            changed.append(_id(record))
    result = metric("M3", len(changed), len(valid), changed, missing)
    result["attribution"] = _counts(attribution)
    return result


def stuck(records: list[dict]) -> dict:
    """M4: no-progress changes and agent failure counts by recorded adapter."""
    status_n, no_progress, agent_n, agent_total, agent_changes, missing = 0, [], 0, 0, [], []
    adapters = Counter()
    for record in records:
        if isinstance(record.get("status"), str) or isinstance(record.get("stops"), list):
            status_n += 1
            if _has_stop(record, "NO_PROGRESS"):
                no_progress.append(_id(record))
        else:
            _miss(missing, record, _gap(record, "status"))
        failures = record.get("agent_failures")
        if not isinstance(failures, list):
            _miss(missing, record, _gap(record, "agent_failures"))
            continue
        agent_n += 1
        if failures:
            agent_changes.append(_id(record))
        agent_total += len(failures)
        for failure in failures:
            adapter = failure.get("adapter") if isinstance(failure, dict) else None
            adapters[adapter if isinstance(adapter, str) and adapter else UNRECORDED] += 1
    changes = _unique(no_progress + agent_changes)
    result = metric("M4", len(no_progress), status_n, changes, missing)
    result.update({"no_progress": len(no_progress), "no_progress_changes": no_progress,
                   "agent_failure_total": agent_total, "agent_failure_denominator": agent_n,
                   "agent_failure_value": _ratio(agent_total, agent_n),
                   "agent_failure_changes": agent_changes, "agent_failures": _counts(adapters)})
    return result


def escapes(records: list[dict]) -> dict:
    """M5: passed origins explicitly named by a later fix."""
    escaped_origins = {record.get("origin_change") for record in records
                       if record.get("nature") == "fix"
                       and isinstance(record.get("origin_change"), str)}
    passed, escaped, missing = [], [], []
    by_rows: dict[str, list[tuple[str, bool]]] = {}
    for record in records:
        status = record.get("status")
        if not isinstance(status, str):
            _miss(missing, record, _gap(record, "status"))
            continue
        if status != "PASSED":
            continue
        change_id = _id(record)
        passed.append(change_id)
        is_escape = change_id in escaped_origins
        if is_escape:
            escaped.append(change_id)
        tier = record.get("tier_effective")
        if tier not in TIER_ORDER:
            _miss(missing, record, "NO_TIER_RECORDED")
        else:
            by_rows.setdefault(tier, []).append((change_id, is_escape))
    by = {tier: metric("M5", sum(flag for _, flag in rows), len(rows),
                       [change_id for change_id, flag in rows if flag])
          for tier, rows in sorted(by_rows.items(), key=lambda item: TIER_ORDER[item[0]])}
    return metric("M5", len(escaped), len(passed), escaped, missing, by)


def forced_heavy(records: list[dict]) -> dict:
    """M7: outcomes for changes manually allowed to continue as heavy."""
    cohort, missing = [], []
    for record in records:
        decision = record.get("continue_heavy")
        if decision is True:
            cohort.append(record)
        elif decision is not False:
            _miss(missing, record, _gap(record, "continue_heavy"))
    result = _forced_summary("M7", cohort, missing)
    groups: dict[str, list[dict]] = {}
    for record in cohort:
        tier = _implement_tier(record)
        if tier is None:
            _miss(result["missing"], record, "NO_TIER_RECORDED")
        else:
            groups.setdefault(tier, []).append(record)
    result["by"] = {tier: _forced_summary("M7", rows, [])
                    for tier, rows in sorted(groups.items())}
    return result


def overrule_rate(records: list[dict]) -> dict:
    """M8: overrule decisions per review finding that caused a stop."""
    numerator, denominator, changes, missing = 0, 0, [], []
    for record in records:
        overrules = record.get("overrules")
        stop_findings = record.get("review_stop_findings")
        if not isinstance(overrules, list) or not _nonnegative_int(stop_findings):
            _miss(missing, record, _gap(record, "review_stop_findings"))
            continue
        historical = 0
        valid = True
        for item in overrules:
            findings = item.get("findings") if isinstance(item, dict) else None
            if not isinstance(findings, list):
                valid = False
                break
            historical += len(findings)
        if not valid:
            _miss(missing, record, _gap(record, "overrules"))
            continue
        numerator += len(overrules)
        denominator += stop_findings + historical
        if overrules:
            changes.append(_id(record))
    return metric("M8", numerator, denominator, changes, missing)


def contract_ratio(project: Path, records: list[dict]) -> dict:
    """M9: median contract-to-implementation line ratio from valid Git lineages."""
    rows, missing = [], []
    for record in records:
        commits = record.get("commits") if isinstance(record.get("commits"), dict) else {}
        source, contract, implementation = (
            commits.get("source_head"), commits.get("contract_commit"),
            commits.get("implementation_commit"),
        )
        if not all(isinstance(item, str) and item for item in (source, contract, implementation)):
            _miss(missing, record, "UNREADABLE")
            continue
        try:
            baseline._git(project, "merge-base", "--is-ancestor", source, contract)
            baseline._git(project, "merge-base", "--is-ancestor", contract, implementation)
            contract_lines = _numstat(baseline._git(project, "diff", "--numstat", source, contract))
            implementation_lines = _numstat(
                baseline._git(project, "diff", "--numstat", contract, implementation))
        except (StoryLoopError, OSError, ValueError):
            contract_lines = implementation_lines = None
        if contract_lines is None or implementation_lines in (None, 0):
            _miss(missing, record, "UNREADABLE")
            continue
        rows.append({"change_id": _id(record), "contract": contract_lines,
                     "implementation": implementation_lines,
                     "ratio": contract_lines / implementation_lines,
                     "tier": record.get("tier_effective")})
    result = _median_result("M9", rows, missing)
    known_tiers = sorted({row["tier"] for row in rows if row["tier"] in TIER_ORDER},
                         key=TIER_ORDER.get)
    result["by"] = {tier: _median_result("M9", [row for row in rows if row["tier"] == tier], [])
                    for tier in known_tiers}
    for row in rows:
        if row["tier"] not in TIER_ORDER:
            _miss(result["missing"], {"change_id": row["change_id"]}, "NO_TIER_RECORDED")
    return result


def interventions(records: list[dict]) -> dict:
    """M10: human interventions ordered by descending occurrence count."""
    names = ("answers", "resets", "repairs", "overrules", "supersede",
             "design_approval", "continue_heavy", "manual_acceptance")
    counts = Counter({name: 0 for name in names})
    coverage = Counter({name: 0 for name in names})
    changes, missing, links = [], [], set()
    for record in records:
        before = sum(counts.values())
        _count_integer_fields(record, counts, coverage, missing)
        _count_list_field(record, "overrules", counts, coverage, missing)
        _count_decision(record, "design_approved", "design_approval", counts, coverage, missing)
        _count_decision(record, "continue_heavy", "continue_heavy", counts, coverage, missing)
        _supersede_links(record, links)
        if sum(counts.values()) > before or record.get("supersedes") or record.get("superseded_by"):
            changes.append(_id(record))
    counts["supersede"] = len(links)
    coverage["supersede"] = len(records)
    order = {name: index for index, name in enumerate(names)}
    value = {name: count for name, count in sorted(
        counts.items(), key=lambda item: (-item[1], order[item[0]])) if count}
    return {"id": "M10", "value": value, "numerator": sum(counts.values()),
            "denominator": len(records), "n": len(records), "changes": _unique(changes),
            "missing": missing, "coverage": dict(coverage)}
