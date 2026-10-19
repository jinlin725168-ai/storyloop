"""Pure aggregation for the bounded per-call telemetry ledger."""

from __future__ import annotations

import json
import math

from .telemetry import _finite_number, _valid_skill


NUMERIC = ("cost_usd", "input_tokens", "output_tokens", "turns", "duration_ms",
           "segments", "lanes", "perspectives", "wall_ms")
CATEGORICAL = {"models": "model", "vendors": "vendor", "tiers": "tier_applied", "skills": "skills"}


def summarize_entries(entries: list[dict]) -> dict:
    entries = [entry if isinstance(entry, dict) else {} for entry in entries]
    result = _summary(entries)
    roles = sorted({entry["role"] for entry in entries if isinstance(entry.get("role"), str)})
    result["by_role"] = {
        role: _summary([entry for entry in entries if entry.get("role") == role]) for role in roles
    }
    return result


def _summary(entries: list[dict]) -> dict:
    total = len(entries)
    coverage = {key: {"known": sum(_valid(entry.get(key)) for entry in entries), "total": total}
                for key in NUMERIC}
    coverage.update({result_key: {"known": sum(source_key in entry for entry in entries), "total": total}
                     for result_key, source_key in CATEGORICAL.items()})
    result = {
        "calls": total,
        "failed": sum(bool(entry.get("failed")) for entry in entries),
        "agent_ms": (_total(entries, "wall_ms")
                     if coverage["wall_ms"]["known"] == total else None),
        "coverage": coverage,
        "models": _strings(entries, "model") if coverage["models"]["known"] else None,
        "vendors": _strings(entries, "vendor") if coverage["vendors"]["known"] else None,
        "tiers": _strings(entries, "tier_applied") if coverage["tiers"]["known"] else None,
        "skills": _skills(entries) if coverage["skills"]["known"] else None,
    }
    for key in NUMERIC:
        known = _total(entries, key)
        complete = total > 0 and coverage[key]["known"] == total
        result[key] = known if complete else None
    result["known_cost_usd"] = _total(entries, "cost_usd") if coverage["cost_usd"]["known"] else None
    return result


def _valid(value):
    return _finite_number(value) and value >= 0


def _total(entries, key):
    try:
        values = [float(entry[key]) for entry in entries if _valid(entry.get(key))]
        if entries and not values:
            return None
        value = sum(values)
        return _rounded(value) if math.isfinite(value) else None
    except OverflowError:
        return None


def _strings(entries: list[dict], key: str) -> list[str]:
    return sorted({entry[key] for entry in entries if isinstance(entry.get(key), str)})


def _skills(entries: list[dict]) -> list[dict]:
    distinct = {json.dumps(skill, sort_keys=True, ensure_ascii=False): skill
                for entry in entries if isinstance(entry.get("skills"), list)
                for skill in entry["skills"] if _valid_skill(skill)}
    return [distinct[key] for key in sorted(distinct)]


def _rounded(value: float) -> int | float:
    rounded = round(float(value), 4)
    return int(rounded) if rounded.is_integer() else rounded
