"""Pure aggregation for the bounded per-call telemetry ledger."""

from __future__ import annotations

import json


NUMERIC = ("cost_usd", "input_tokens", "output_tokens", "turns")
CATEGORICAL = {"models": "model", "vendors": "vendor", "tiers": "tier_applied", "skills": "skills"}


def summarize_entries(entries: list[dict]) -> dict:
    result = _summary(entries)
    roles = sorted({entry["role"] for entry in entries})
    result["by_role"] = {
        role: _summary([entry for entry in entries if entry["role"] == role]) for role in roles
    }
    return result


def _summary(entries: list[dict]) -> dict:
    total = len(entries)
    coverage = {key: {"known": sum(key in entry for entry in entries), "total": total}
                for key in NUMERIC}
    coverage.update({result_key: {"known": sum(source_key in entry for entry in entries), "total": total}
                     for result_key, source_key in CATEGORICAL.items()})
    result = {
        "calls": total,
        "failed": sum(bool(entry.get("failed")) for entry in entries),
        "agent_ms": sum(int(entry.get("wall_ms", 0)) for entry in entries),
        "coverage": coverage,
        "models": _strings(entries, "model") if coverage["models"]["known"] else None,
        "vendors": _strings(entries, "vendor") if coverage["vendors"]["known"] else None,
        "tiers": _strings(entries, "tier_applied") if coverage["tiers"]["known"] else None,
        "skills": _skills(entries) if coverage["skills"]["known"] else None,
    }
    for key in NUMERIC:
        known = sum(float(entry[key]) for entry in entries if key in entry)
        complete = total > 0 and coverage[key]["known"] == total
        result[key] = _rounded(known) if complete else None
    result["known_cost_usd"] = (_rounded(
        sum(float(entry["cost_usd"]) for entry in entries if "cost_usd" in entry)
    ) if coverage["cost_usd"]["known"] else None)
    return result


def _strings(entries: list[dict], key: str) -> list[str]:
    return sorted({entry[key] for entry in entries if isinstance(entry.get(key), str)})


def _skills(entries: list[dict]) -> list[dict]:
    distinct = {json.dumps(skill, sort_keys=True, ensure_ascii=False): skill
                for entry in entries for skill in entry.get("skills", [])}
    return [distinct[key] for key in sorted(distinct)]


def _rounded(value: float) -> int | float:
    rounded = round(float(value), 4)
    return int(rounded) if rounded.is_integer() else rounded
