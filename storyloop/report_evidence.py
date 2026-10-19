"""Bounded, read-only evidence snapshots for acceptance report presentation."""

from __future__ import annotations

import hashlib
from itertools import islice
import json
import math
from pathlib import Path
import re

from .telemetry import ledger_entries, summarize
from .processutil import redact
from .report_git import git_snapshot
from .report_commands import execution_commands
from .telemetry_summary import NUMERIC, _valid


MAX_GIT_BYTES = 1_000_000
MAX_RUN_BYTES = 2_000_000
MAX_LEDGER_BYTES = 4_000_000
MAX_META_BYTES = 256_000
MAX_ENTRIES = 1_000
MAX_CALLS = 500
MAX_LIST = 200
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}")
RUN_FIELDS = frozenset({
    "version", "change_id", "status", "attempt", "feature_branch", "source_branch",
    "source_head", "contract_digest", "started_at", "phase", "planning", "routing",
    "facts", "failures", "agent_failures", "approvals", "overrules", "repairs", "resets",
    "reset", "review", "design", "boundary", "verified", "mode", "candidate_sha256",
    "interrupted_status", "review_disposition",
    "implementation_commit", "integration_commit", "contract_commit", "report_commit",
})
ENTRY_FIELDS = frozenset({
    "role", "wall_ms", "at", "failed", "call", "session", "cost_usd", "turns",
    "duration_ms", "input_tokens", "output_tokens", "segments", "lanes", "perspectives",
    "model", "vendor", "tier_applied", "skills", "tier_requested", "adapter",
    "degraded", "path_guard", "subagents_applied",
})
META_FIELDS = frozenset({
    "version", "call", "role", "ledger", "adapter", "started_at", "status", "wall_ms",
    "sessions", "repairs", "semantic_repair", "error", "code", "schema_errors",
})


def collect_evidence(project: Path, change: dict, report: dict) -> dict:
    """Collect only bounded, allowlisted persisted facts needed by report rendering."""
    root = Path(project).resolve()
    change_id = _identifier(change.get("change_id"))
    run, run_status, run_truncated = _load_run(root, change_id)
    entries, telemetry, entry_status, truncated = _load_entries(root, change_id)
    calls, calls_truncated = _load_calls(
        root, change_id, {item.get("call") for item in entries if isinstance(item.get("call"), str)})
    git = git_snapshot(root, run, report, MAX_GIT_BYTES, MAX_ENTRIES)
    protected = _protected_evidence(root, change_id, report)
    answers, answers_status, answers_truncated = _load_answers(root, change_id)
    return {
        "run": run,
        "role_downgrades": _role_downgrades(root),
        "answers": answers,
        "entries": {"ledger": entries, "calls": calls,
                    "truncated": {"ledger": truncated, "calls": calls_truncated}},
        "telemetry": telemetry,
        "verification": execution_commands(root, change_id, _read_object, MAX_RUN_BYTES),
        "git": git,
        "availability": {"run": run_status, "run_truncated": run_truncated,
                         "entries": entry_status, "answers": answers_status,
                         "answers_truncated": answers_truncated,
                         "telemetry": entry_status, "evidence": protected},
    }


def _identifier(value: object) -> str | None:
    return value if isinstance(value, str) and IDENTIFIER.fullmatch(value) else None


def _safe_file(root: Path, path: Path, limit: int) -> bool:
    try:
        return (not path.is_symlink() and path.is_file() and path.stat().st_size <= limit
                and path.resolve().is_relative_to(root))
    except OSError:
        return False


def _read_object(root: Path, path: Path, limit: int) -> dict | None:
    if not _safe_file(root, path, limit):
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _load_run(root: Path, change_id: str | None) -> tuple[dict | None, str, bool]:
    if change_id is None:
        return None, "NOT_AVAILABLE", False
    path = root / ".storyloop/state/runs" / f"{change_id}.json"
    value = _read_object(root, path, MAX_RUN_BYTES)
    if value is None or value.get("change_id") != change_id:
        return None, "NOT_AVAILABLE", path.exists()
    bounded, truncated = _bounded({key: value[key] for key in RUN_FIELDS if key in value})
    return bounded, "AVAILABLE", truncated


def _bounded(value: object, depth: int = 0) -> tuple[object, bool]:
    if depth >= 8:
        return None, True
    if isinstance(value, str):
        return redact(value[:20_000]), len(value) > 20_000
    if isinstance(value, list):
        converted = [_bounded(item, depth + 1) for item in value[:MAX_LIST]]
        return [item for item, _ in converted], len(value) > MAX_LIST or any(flag for _, flag in converted)
    if isinstance(value, dict):
        items = list(value.items())[:MAX_LIST]
        converted = [(str(key)[:200], _bounded(item, depth + 1)) for key, item in items]
        return {key: item for key, (item, _) in converted}, (len(value) > MAX_LIST
                or any(flag for _, (_, flag) in converted))
    if isinstance(value, float) and not math.isfinite(value):
        return None, True
    return value if value is None or isinstance(value, (bool, int, float)) else None, False


def _load_entries(root: Path, change_id: str | None) -> tuple[list, dict | None, str, bool]:
    if change_id is None:
        return [], None, "NOT_AVAILABLE", False
    path = root / ".storyloop/state/telemetry" / f"{change_id}.json"
    if not _safe_file(root, path, MAX_LEDGER_BYTES):
        return [], None, "NOT_AVAILABLE", path.exists()
    try:
        raw = ledger_entries(root, change_id)
        totals = summarize(root, change_id)
    except (OSError, RuntimeError, ValueError, KeyError, TypeError, AttributeError, OverflowError):
        return [], None, "NOT_AVAILABLE", True
    truncated = len(raw) > MAX_ENTRIES
    bounded = raw[:MAX_ENTRIES]
    projected = [{key: item[key] for key in ENTRY_FIELDS if key in item}
                 for item in bounded if isinstance(item, dict)]
    malformed = any(not _valid_entry(item) for item in bounded)
    converted = [_bounded(_diagnostic_record(item)) for item in projected]
    totals, totals_truncated = _bounded(totals)
    clean = [item for item, _ in converted]
    incomplete = (malformed or truncated or totals_truncated or len(clean) != len(bounded)
                  or any(flag for _, flag in converted))
    return clean, totals, "NOT_AVAILABLE" if malformed else "AVAILABLE", incomplete


def _load_calls(root: Path, change_id: str | None, ledger_ids: set[str]) -> tuple[list[dict], bool]:
    calls_root = root / ".storyloop/state/telemetry/calls"
    if calls_root.is_symlink() or not calls_root.is_dir():
        return [], False
    calls, incomplete = [], False
    try:
        sampled = list(islice(calls_root.iterdir(), MAX_CALLS + 1))
        children = sorted(sampled[:MAX_CALLS], key=lambda item: item.name)
    except OSError:
        return [], True
    for folder in children:
        call_id = _identifier(folder.name)
        value = None if call_id is None or folder.is_symlink() else _read_object(
            root, folder / "meta.json", MAX_META_BYTES)
        if value is None or value.get("call") != call_id:
            incomplete = (incomplete or folder.name in ledger_ids
                          or isinstance(value, dict) and value.get("ledger") == change_id)
            continue
        adopted = (value.get("ledger") is None and value.get("role") == "product"
                   and call_id in ledger_ids)
        if value.get("ledger") != change_id and not adopted:
            incomplete = incomplete or call_id in ledger_ids
            continue
        malformed = (not _identifier(value.get("role"))
                     or not isinstance(value.get("status"), str))
        projected, truncated = _bounded({key: value[key] for key in META_FIELDS if key in value})
        calls.append(_diagnostic_record(projected))
        incomplete = incomplete or truncated or malformed
    return calls, incomplete or len(sampled) > MAX_CALLS


def _valid_entry(value):
    if not isinstance(value, dict) or not _identifier(value.get("role")):
        return False
    if "call" in value and not _identifier(value["call"]):
        return False
    return all(_valid(value[key]) for key in NUMERIC if key in value)


def _diagnostic_record(value):
    result = dict(value)
    for key in ("call", "role", "ledger", "status"):
        if key in result and result[key] is not None and not _identifier(result[key]):
            result[key] = None
            result["invalid_fields"] = [*result.get("invalid_fields", []), key]
    return result


def _protected_evidence(root: Path, change_id: str | None, report: dict) -> dict:
    declared = {item.get("evidence_sha256") for item in report.get("traceability") or []
                if isinstance(item, dict) and isinstance(item.get("evidence_sha256"), str)}
    acceptance = (report.get("layers") or {}).get("acceptance") or {}
    if isinstance(acceptance.get("evidence_sha256"), str):
        declared.add(acceptance["evidence_sha256"])
    path = root / ".storyloop/evidence" / str(change_id) / "acceptance-result.json"
    declared_path = acceptance.get("evidence_path")
    path_matches = _declared_path_matches(root, path, declared_path)
    if change_id is None or not _safe_file(root, path, MAX_RUN_BYTES):
        return {"availability": "NOT_AVAILABLE", "path": None, "actual_sha256": None,
                "declared_sha256": sorted(declared), "declared_path_matches": path_matches}
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"availability": "AVAILABLE", "path": path.relative_to(root).as_posix(),
            "actual_sha256": actual, "declared_sha256": sorted(declared),
            "declared_path_matches": path_matches}


def _declared_path_matches(root: Path, expected: Path, declared: object) -> bool | None:
    if declared is None:
        return None
    if not isinstance(declared, str):
        return False
    candidate = Path(declared)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        return not candidate.is_symlink() and candidate.resolve() == expected.resolve()
    except OSError:
        return False


def _load_answers(root: Path, change_id: str | None) -> tuple[list | None, str, bool]:
    if change_id is None:
        return None, "NOT_AVAILABLE", False
    path = root / ".storyloop/state/answers" / f"{change_id}.json"
    value = _read_object(root, path, MAX_RUN_BYTES)
    if (value is None or value.get("version") != 1 or value.get("change_id") != change_id
            or not isinstance(value.get("entries"), list)):
        return None, "NOT_AVAILABLE", False
    fields = {"attempt", "question", "answer", "answered_at"}
    entries = value["entries"]
    projected = [{key: record[key] for key in fields if key in record}
                 for record in entries[:MAX_LIST] if isinstance(record, dict)]
    bounded, truncated = _bounded(projected)
    return bounded, "AVAILABLE", truncated or len(entries) > MAX_LIST or len(projected) != len(entries)


def _role_downgrades(root):
    config = _read_object(root, root / ".storyloop/config.json", MAX_RUN_BYTES) or {}
    roles = config.get("roles") or {}
    if not isinstance(roles, dict):
        return []
    rows = []
    for role in ("product", "acceptance", "design", "review"):
        setting = roles.get(role) or {}
        if not isinstance(setting, dict):
            continue
        tier = setting.get("tier")
        lowered = tier == "standard" or isinstance(tier, dict) and "standard" in tier.values()
        if lowered and isinstance(setting.get("reason"), str) and setting["reason"].strip():
            rows.append({"role": role, "tier": tier, "reason": setting["reason"].strip()})
    return _bounded(rows)[0]
