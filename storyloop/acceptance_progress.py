"""Persist comparable blocking evidence across automatic acceptance rewrites."""

from __future__ import annotations

import re

PROGRESS_CATEGORIES = frozenset({
    "ORACLE_STRENGTH_PRECHECK", "MUST_HOLD_RED",
    "MANIFEST_OR_SOURCE_BOUNDARY", "UNVERIFIED_ENTRY_USE",
})
DEFAULT_AUTO_RETRY_ROUND_LIMIT = 10


def blocking_signatures(acceptance: dict) -> tuple[list[dict], list[str]]:
    """Use stable review IDs for precheck findings and concrete rules for deterministic defects."""
    category = acceptance.get("defect_category") or "UNKNOWN"
    findings = (acceptance.get("precheck") or {}).get("findings") or []
    if category == "ORACLE_STRENGTH_PRECHECK":
        blocking = [item for item in findings if item.get("blocking") is True]
        signatures = [{"category": category, "criterion_id": item.get("criterion_id") or "",
                       "path": _path(item.get("location") or ""),
                       "rule": item.get("id") or item.get("required_change") or item.get("message") or ""}
                      for item in blocking]
        return _unique(signatures), sorted({str(item["id"]) for item in blocking if item.get("id")})

    blockers = acceptance.get("blockers") or []
    if blockers:
        signatures = [{"category": category, "criterion_id": item.get("criterion_id") or "",
                       "path": _path(item.get("path") or ""),
                       "rule": item.get("mechanism") or ""} for item in blockers]
        return _unique(signatures), sorted({str(item["test_id"]) for item in blockers
                                            if item.get("test_id")})

    criteria = acceptance.get("criteria_failing") or acceptance.get("criterion_ids") or []
    entry_findings = acceptance.get("entry_use_findings") or []
    if entry_findings:
        signatures = [{"category": category, "criterion_id": item.get("criterion_id") or
                       ",".join(item.get("criterion_ids") or criteria) or item.get("test_id") or "",
                       "path": _path(item.get("path") or ""),
                       "rule": item.get("entry_id") or item.get("required_call") or ""}
                      for item in entry_findings]
        return _unique(signatures), sorted({str(item.get("test_id")) for item in entry_findings})

    diagnostics = acceptance.get("diagnostics") or []
    signatures = [{"category": category, "criterion_id": ",".join(criteria) or
                   item.get("criterion_id") or item.get("case_id") or "",
                   "path": _path(item.get("path") or item.get("case_id") or ""),
                   "rule": str(item.get("kind") or "") + ":" + str(item.get("message") or "")[:500]}
                  for item in diagnostics if isinstance(item, dict)]
    if not signatures:
        signatures = [{"category": category, "criterion_id": ",".join(criteria),
                       "path": "", "rule": ",".join(acceptance.get("test_ids") or [])}]
    return _unique(signatures), sorted({str(item.get("case_id")) for item in diagnostics
                                        if isinstance(item, dict) and item.get("case_id")})


def remember_round(state: dict, acceptance: dict) -> tuple[str | None, list[str]]:
    """A replay of the same repair count does not create a new assessment round."""
    signatures, finding_ids = blocking_signatures(acceptance)
    progress = state.setdefault("acceptance_retry", {"version": 1, "rounds": []})
    rounds = progress.setdefault("rounds", [])
    repair_count = len(state.get("repairs") or [])
    if rounds and rounds[-1].get("repair_count") == repair_count:
        return ("exhausted" if progress.get("exhausted") else None), finding_ids
    previous = rounds[-1]["signatures"] if rounds else None
    rounds.append({"repair_count": repair_count, "signatures": signatures,
                   "finding_ids": finding_ids})
    if previous == signatures:
        progress["exhausted"] = True
        return "no_progress", finding_ids
    return None, finding_ids


def _path(location: str) -> str:
    return re.sub(r":\d+(?::\d+)?$", "", str(location))


def _unique(signatures: list[dict]) -> list[dict]:
    return [dict(item) for item in sorted({tuple(sorted(item.items())) for item in signatures})]
