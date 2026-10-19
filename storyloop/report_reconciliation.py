"""Pure reconciliation of persisted run evidence with a proposed report."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from .report_evidence import _identifier


LAYERS = frozenset({"unit", "integration", "acceptance", "smoke", "checks"})
TERMINAL_CALLS = frozenset({"OK", "FAILED", "WAITING"})


def reconcile(project: Path, change: dict, report: dict, evidence: dict) -> dict:
    """Compare actual records in both directions without changing any workflow authority."""
    del project
    entries = (evidence.get("entries") or {}).get("ledger") or []
    calls = (evidence.get("entries") or {}).get("calls") or []
    limitations = ["Theoretical schedule and expected call count are not persisted; only actual calls are reconciled."]
    checks = [_layer_check(report), _evidence_check(evidence), _commit_check(report),
              _source_check(evidence)]
    legacy = not entries or any(not item.get("call") for item in entries)
    if legacy:
        limitations.append("Legacy or empty ledger has no complete call identifiers for bidirectional reconciliation.")
        checks.append(_check("calls", "NOT_AVAILABLE", "call identifiers are absent"))
    checks.extend(_call_checks(change, entries, calls, evidence))
    return {"status": _status(checks), "checks": checks, "limitations": limitations}


def _layer_check(report: dict) -> dict:
    actual = set((report.get("layers") or {}).keys())
    return _check("layers", "MATCH" if actual == LAYERS else "MISMATCH",
                  "five verification layers are exact", sorted(LAYERS), sorted(actual))


def _evidence_check(evidence: dict) -> dict:
    item = (evidence.get("availability") or {}).get("evidence") or {}
    actual, declared = item.get("actual_sha256"), item.get("declared_sha256") or []
    if item.get("availability") != "AVAILABLE" and not declared:
        return _check("protected_evidence", "PENDING", "no protected evidence was declared")
    path_matches = item.get("declared_path_matches")
    if path_matches is None:
        return _check("protected_evidence", "NOT_AVAILABLE",
                      "protected evidence path was not explicitly declared", declared, actual)
    status = "MATCH" if (actual and declared and set(declared) == {actual}
                         and path_matches is not False) else "MISMATCH"
    return _check("protected_evidence", status, "actual digest must equal every declaration",
                  declared, actual)


def _commit_check(report: dict) -> dict:
    pending = [name for name in ("report_commit", "merge_commit") if not report.get(name)]
    return _check("future_commits", "PENDING" if pending else "MATCH",
                  "commits not yet created remain pending", [], pending)


def _source_check(evidence: dict) -> dict:
    availability = evidence.get("availability") or {}
    git = evidence.get("git") or {}
    if availability.get("run_truncated") or git.get("truncated"):
        return _check("source_snapshot", "MISMATCH", "run and Git snapshots must be complete")
    if availability.get("run") != "AVAILABLE" or git.get("availability") != "AVAILABLE":
        return _check("source_snapshot", "NOT_AVAILABLE", "run or anchored Git source is unavailable")
    return _check("source_snapshot", "MATCH", "run and anchored Git sources are complete")


def _call_checks(change: dict, entries: list[dict], calls: list[dict], evidence: dict) -> list[dict]:
    malformed = any(item.get("invalid_fields") for item in entries + calls)
    entries = [item for item in entries if _identifier(item.get("call"))]
    calls = [item for item in calls if _identifier(item.get("call"))]
    ledger_ids = [item.get("call") for item in entries]
    meta_ids = [item.get("call") for item in calls]
    duplicates = sorted(key for key, count in Counter(ledger_ids).items() if count > 1)
    missing = sorted(set(ledger_ids) - set(meta_ids))
    orphan = sorted(set(meta_ids) - set(ledger_ids))
    checks = [
        _check("duplicate_calls", "MISMATCH" if duplicates else "MATCH", "ledger call ids are unique", [], duplicates),
        _check("missing_call_meta", "MISMATCH" if missing else "MATCH", "each ledger call has meta", [], missing),
        _check("orphan_call_meta", "MISMATCH" if orphan else "MATCH", "each meta belongs to ledger", [], orphan),
    ]
    unfinished = [item["call"] for item in calls if not isinstance(item.get("status"), str)
                  or item["status"] not in TERMINAL_CALLS]
    checks.append(_check("call_lifecycle", "MISMATCH" if unfinished else "MATCH",
                         "all persisted calls have terminal lifecycle", [], unfinished))
    checks.append(_check("diagnostic_fields", "MISMATCH" if malformed else "MATCH",
                         "diagnostic identifiers have valid shapes"))
    checks.append(_pair_check(change.get("change_id"), entries, calls))
    truncated = (evidence.get("entries") or {}).get("truncated") or {}
    checks.append(_check("bounded_records", "MISMATCH" if any(truncated.values()) else "MATCH",
                         "bounded snapshot is complete", False, truncated))
    return checks


def _pair_check(change_id: object, entries: list[dict], calls: list[dict]) -> dict:
    by_id = {item.get("call"): item for item in calls}
    problems = []
    for entry in entries:
        meta = by_id.get(entry.get("call"))
        if meta is None:
            continue
        adopted = (entry.get("role") == "product" and meta.get("ledger") is None)
        if meta.get("role") != entry.get("role") or (meta.get("ledger") != change_id and not adopted):
            problems.append(entry.get("call"))
        elif not isinstance(meta.get("status"), str) or meta["status"] not in TERMINAL_CALLS:
            problems.append(entry.get("call"))
    return _check("call_pairs", "MISMATCH" if problems else "MATCH",
                  "role, owning ledger and terminal lifecycle agree", [], sorted(set(problems)))


def _check(identifier: str, status: str, reason: str,
           expected: object = None, actual: object = None) -> dict:
    return {"id": identifier, "status": status, "reason": reason,
            "expected": expected, "actual": actual}


def _status(checks: list[dict]) -> str:
    statuses = {item["status"] for item in checks}
    if "MISMATCH" in statuses:
        return "MISMATCH"
    return "NOT_AVAILABLE" if "NOT_AVAILABLE" in statuses else "MATCH"
