"""Shared constructors and parsers for metrics_compute."""

from __future__ import annotations

from collections import Counter
from statistics import median
from typing import Any


TIER_ORDER = {"light": 0, "standard": 1, "heavy": 2}
UNRECORDED = "未记录"
FAILED_OUTCOMES = frozenset({"FAILED", "BUDGET_EXHAUSTED", "NO_PROGRESS", "SUPERSEDED"})
M7_OUTCOMES = frozenset({"PASSED"}) | FAILED_OUTCOMES


def metric(
    id: str, numerator: int | float, denominator: int, changes: list[str],
    missing: list[dict] | None = None, by: dict | None = None,
) -> dict:
    """Build the common ratio shape without turning an empty sample into zero."""
    result = {
        "id": id,
        "value": _round(numerator / denominator) if denominator else None,
        "numerator": numerator,
        "denominator": denominator,
        "n": denominator,
        "changes": _unique(changes),
        "missing": list(missing or []),
    }
    if by is not None:
        result["by"] = by
    if denominator == 0:
        result["reason"] = "NO_SAMPLE"
    return result


def _forced_summary(id: str, records: list[dict], missing: list[dict]) -> dict:
    outcomes, known, failures, attempt_values, attempt_ids = Counter(), [], [], [], []
    for record in records:
        status = record.get("status")
        if isinstance(status, str) and status in M7_OUTCOMES:
            outcomes[status] += 1
            known.append(_id(record))
            if status in FAILED_OUTCOMES:
                failures.append(_id(record))
        elif isinstance(status, str):
            _miss(missing, record, "NO_SAMPLE")
        else:
            _miss(missing, record, _gap(record, "status"))
        value = record.get("attempts")
        if _nonnegative_int(value):
            attempt_values.append(value)
            attempt_ids.append(_id(record))
        else:
            _miss(missing, record, _gap(record, "attempts"))
    result = metric(id, len(failures), len(known), known, missing)
    result.update({"outcomes": _counts(outcomes), "failure_changes": failures,
                   "mean_attempts": _mean(attempt_values),
                   "attempt_denominator": len(attempt_values), "attempt_changes": attempt_ids})
    return result


def _median_result(id: str, rows: list[dict], missing: list[dict]) -> dict:
    result = {"id": id, "value": _round(median(row["ratio"] for row in rows)) if rows else None,
              "numerator": sum(row["contract"] for row in rows),
              "denominator": sum(row["implementation"] for row in rows), "n": len(rows),
              "changes": [row["change_id"] for row in rows], "missing": list(missing)}
    if not rows:
        result["reason"] = "NO_SAMPLE"
    return result


def _upgraded(history: list) -> bool | None:
    malformed = False
    for item in history:
        if not isinstance(item, dict):
            malformed = True
            continue
        attempt, source, target = item.get("attempt"), item.get("from_tier"), item.get("to_tier")
        if not _nonnegative_int(attempt) or source not in TIER_ORDER or target not in TIER_ORDER:
            malformed = True
        elif attempt > 0 and TIER_ORDER[target] > TIER_ORDER[source]:
            return True
    return None if malformed else False


def _upgrade_rule_ids(history: list) -> list[str]:
    """Return persisted rule IDs only from actual in-run upward transitions."""
    identifiers = []
    for item in history:
        if not isinstance(item, dict) or not _nonnegative_int(item.get("attempt")):
            continue
        source, target = item.get("from_tier"), item.get("to_tier")
        if item["attempt"] <= 0 or source not in TIER_ORDER or target not in TIER_ORDER:
            continue
        if TIER_ORDER[target] <= TIER_ORDER[source] or not isinstance(item.get("reason"), list):
            continue
        identifiers.extend(reason["rule_id"] for reason in item["reason"]
                           if isinstance(reason, dict)
                           and isinstance(reason.get("rule_id"), str) and reason["rule_id"])
    return identifiers


def _last_signatures(failures: object) -> tuple[str, str] | None:
    if not isinstance(failures, list) or len(failures) < 2:
        return None
    values = []
    for item in failures[-2:]:
        signature = item.get("signature") if isinstance(item, dict) else None
        if not isinstance(signature, str) or not signature:
            return None
        values.append(signature)
    return values[0], values[1]


def _implement_tier(record: dict) -> str | None:
    ledger = record.get("ledger")
    if isinstance(ledger, list):
        tiers = [item.get("tier_applied") for item in ledger
                 if isinstance(item, dict) and item.get("role") == "implement"
                 and isinstance(item.get("tier_applied"), str) and item.get("tier_applied")]
        if tiers:
            return tiers[-1]
    by_role = record.get("by_role")
    implement = by_role.get("implement") if isinstance(by_role, dict) else None
    tiers = implement.get("tiers") if isinstance(implement, dict) else None
    if isinstance(tiers, list):
        actual = sorted({tier for tier in tiers if isinstance(tier, str) and tier})
        if actual:
            return actual[0] if len(actual) == 1 else "+".join(actual)
    return None


def _numstat(output: str) -> int | None:
    total = 0
    for line in output.splitlines():
        if not line:
            continue
        fields = line.split("\t", 2)
        if len(fields) != 3 or not fields[0].isdigit() or not fields[1].isdigit():
            return None
        total += int(fields[0]) + int(fields[1])
    return total


def _count_integer_fields(record, counts, coverage, missing) -> None:
    for name in ("answers", "resets", "repairs", "manual_acceptance"):
        value = record.get(name)
        if _nonnegative_int(value):
            counts[name] += value
            coverage[name] += 1
        else:
            _miss(missing, record, _gap(record, name))


def _count_list_field(record, name, counts, coverage, missing) -> None:
    value = record.get(name)
    if isinstance(value, list):
        counts[name] += len(value)
        coverage[name] += 1
    else:
        _miss(missing, record, _gap(record, name))


def _count_decision(record, source, result, counts, coverage, missing) -> None:
    value = record.get(source)
    if isinstance(value, bool):
        counts[result] += int(value)
        coverage[result] += 1
    else:
        _miss(missing, record, _gap(record, source))


def _supersede_links(record: dict, links: set[tuple[str, str]]) -> None:
    change_id, previous, following = _id(record), record.get("supersedes"), record.get("superseded_by")
    if isinstance(previous, str) and previous:
        links.add((previous, change_id))
    if isinstance(following, str) and following:
        links.add((change_id, following))


def _has_stop(record: dict, status: str) -> bool:
    stops = record.get("stops")
    return record.get("status") == status or isinstance(stops, (list, set, tuple)) and status in stops


def _gap(record: dict, field: str) -> str:
    gaps = record.get("gaps")
    reason = gaps.get(field) if isinstance(gaps, dict) else None
    return reason if reason in {"NO_ROUTING", "NO_TIER_RECORDED", "UNREADABLE"} else "UNREADABLE"


def _miss(missing: list[dict], record: dict, reason: str) -> None:
    item = {"change_id": _id(record), "reason": reason}
    if item not in missing:
        missing.append(item)


def _id(record: dict) -> str:
    value = record.get("change_id")
    return value if isinstance(value, str) else ""


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _counts(values: Counter) -> dict:
    return {name: count for name, count in sorted(values.items(), key=lambda item: (-item[1], item[0]))}


def _ratio(numerator: int | float, denominator: int) -> float | None:
    return _round(numerator / denominator) if denominator else None


def _mean(values: list[int]) -> float | None:
    return _round(sum(values) / len(values)) if values else None


def _round(value: float) -> float:
    return round(float(value), 4)


def _nonnegative_int(value: Any) -> bool:
    return type(value) is int and value >= 0
