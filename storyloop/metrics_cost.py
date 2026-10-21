"""M11 aggregation from report summaries with raw-ledger fallback."""

from __future__ import annotations

import math

from .metrics_compute_support import (
    UNRECORDED, _add_missing, _gap, _id, _nonnegative_int, _ratio, _unique,
)


def cost(records: list[dict]) -> dict:
    """Return known cost and time, preferring report role summaries."""
    rows: dict[str, dict] = {}
    changes, missing = [], []
    for record in records:
        ledger = record.get("ledger")
        entries = ledger if isinstance(ledger, list) else []
        summaries = record.get("by_role")
        summarized: set[str] = set()
        record_calls = 0
        if isinstance(summaries, dict):
            for role, summary in summaries.items():
                if not isinstance(role, str) or not role or not isinstance(summary, dict):
                    continue
                row = _summary_row(summary, [item for item in entries
                                             if isinstance(item, dict) and item.get("role") == role])
                if row is not None:
                    _merge_row(rows.setdefault(f"{role}|{UNRECORDED}", _empty_row()), row)
                    summarized.add(role)
                    record_calls += row["calls"]
        for entry in entries:
            if not isinstance(entry, dict):
                _add_missing(missing, _id(record), "UNREADABLE")
                continue
            role = _label(entry.get("role"))
            if role in summarized:
                continue
            key = f"{role}|{_label(entry.get('tier_applied'))}"
            _add_entry(rows.setdefault(key, _empty_row()), entry)
            record_calls += 1
        if record_calls:
            changes.append(_id(record))
        elif not isinstance(summaries, dict) and not isinstance(ledger, list):
            _add_missing(missing, _id(record), _gap(record, "ledger"))
    return _result(rows, changes, missing)


def _summary_row(summary: dict, entries: list[dict]) -> dict | None:
    calls = summary.get("calls")
    calls = calls if _nonnegative_int(calls) else len(entries) if entries else None
    if calls is None:
        return None
    agent_ms = summary.get("agent_ms")
    if not _finite_nonnegative(agent_ms):
        agent_ms = sum(item["wall_ms"] for item in entries
                       if _finite_nonnegative(item.get("wall_ms")))
    known_cost = summary.get("known_cost_usd")
    if not _finite_nonnegative(known_cost):
        known_cost = sum(item["cost_usd"] for item in entries
                         if _finite_nonnegative(item.get("cost_usd")))
    known_calls = _summary_known_calls(summary, calls, entries)
    return {"calls": calls, "agent_ms": agent_ms, "cost_usd": known_cost,
            "known_cost_calls": known_calls}


def _summary_known_calls(summary: dict, calls: int, entries: list[dict]) -> int:
    coverage = summary.get("coverage")
    cost = coverage.get("cost_usd") if isinstance(coverage, dict) else None
    known = cost.get("known") if isinstance(cost, dict) else None
    total = cost.get("total") if isinstance(cost, dict) else None
    if _nonnegative_int(known) and _nonnegative_int(total) and known <= total == calls:
        return known
    return min(calls, sum(_finite_nonnegative(item.get("cost_usd")) for item in entries))


def _result(rows: dict[str, dict], changes: list[str], missing: list[dict]) -> dict:
    by = {key: _public_row(value) for key, value in sorted(rows.items()) if value["calls"]}
    calls = sum(row["calls"] for row in by.values())
    known = sum(rows[key]["known_cost_calls"] for key in by)
    known_cost = round(sum(row["cost_usd"] for row in by.values()), 4)
    result = {"id": "M11", "value": known_cost if calls else None,
              "numerator": known_cost, "denominator": calls, "n": calls,
              "changes": _unique(changes), "missing": missing, "calls": calls,
              "agent_ms": sum(row["agent_ms"] for row in by.values()),
              "known_cost_calls": known, "cost_coverage": _ratio(known, calls), "by": by}
    if not calls:
        result["reason"] = "NO_SAMPLE"
    return result


def _add_entry(row: dict, entry: dict) -> None:
    row["calls"] += 1
    if _finite_nonnegative(entry.get("wall_ms")):
        row["agent_ms"] += entry["wall_ms"]
    if _finite_nonnegative(entry.get("cost_usd")):
        row["cost_usd"] += entry["cost_usd"]
        row["known_cost_calls"] += 1


def _merge_row(target: dict, source: dict) -> None:
    for key in ("calls", "agent_ms", "cost_usd", "known_cost_calls"):
        target[key] += source[key]


def _public_row(row: dict) -> dict:
    return {"calls": row["calls"], "agent_ms": row["agent_ms"],
            "cost_usd": round(row["cost_usd"], 4),
            "known_cost_calls": row["known_cost_calls"],
            "cost_coverage": _ratio(row["known_cost_calls"], row["calls"])}


def _empty_row() -> dict:
    return {"calls": 0, "agent_ms": 0, "cost_usd": 0.0, "known_cost_calls": 0}


def _label(value: object) -> str:
    return value if isinstance(value, str) and value else UNRECORDED


def _finite_nonnegative(value: object) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and value >= 0
