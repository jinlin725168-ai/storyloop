"""Build the diagnostic memory section of a frozen acceptance report."""

from __future__ import annotations

from pathlib import Path
import re

from .baseline import _git
from .lesson_store import lessons_digest
from .path_security import safe_target
from .report_items import item
from .retrospective import detect_signals
from .call_log import call_folder
from .storage import load_json


_CALL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}")
_FIELDS = ("lessons_provided", "lessons_claimed", "lessons_unknown")


def memory_items(project: Path, change: dict, report: dict, evidence: dict) -> list[dict]:
    """Describe supplied and claimed lessons without treating agent claims as verified facts."""
    project = Path(project).resolve()
    digest, digest_error = _digest(project, evidence)
    skipped = _skipped_calls(evidence)
    if (not (project / "openspec/lessons").is_dir() and digest is None
            and not digest_error and not skipped):
        return [item("memory", reason="LESSONS_NOT_CONFIGURED")]
    rows, error = _lesson_rows(project, evidence)
    summary = {"provided": _ids(rows, "provided"), "claimed": _ids(rows, "claimed"),
               "unknown": _ids(rows, "unknown"), "digest": digest}
    summary_reason = "代理声称使用了未提供的条目" if summary["unknown"] else digest_error
    provided = item("lessons_provided", rows if error is None else None,
                    reason=error or "AGENT_CLAIMED_NOT_VERIFIED")
    summarized = item("lessons_summary", summary, reason=summary_reason)
    try:
        signals = item("lesson_signals", detect_signals(project, change, evidence.get("run") or {}))
    except Exception as exc:
        signals = item("lesson_signals", reason="SIGNALS_UNAVAILABLE: " + str(exc))
    diagnostics = [item("lessons_skipped", skipped, reason="经验整层跳过")] if skipped else []
    return [provided, summarized, *diagnostics, signals]


def _skipped_calls(evidence: dict) -> list[dict]:
    return [{"call": call.get("call"), "role": call.get("role"),
             "reason": call["lessons_skipped"]}
            for call in (evidence.get("entries") or {}).get("calls") or []
            if isinstance(call, dict) and isinstance(call.get("lessons_skipped"), str)
            and call["lessons_skipped"].strip()]


def _lesson_rows(project: Path, evidence: dict) -> tuple[list[dict], str | None]:
    rows = []
    ledger = (evidence.get("entries") or {}).get("ledger") or []
    for row in ledger:
        if not isinstance(row, dict):
            return [], "COMPLETE_EVIDENCE_INVALID"
        complete = row
        if row.get("lessons_truncated"):
            complete = _complete_meta(project, row.get("call"))
            if complete is None:
                return [], "COMPLETE_EVIDENCE_MISSING_OR_INVALID"
        arrays = [complete.get(name, []) for name in _FIELDS]
        if any(not isinstance(value, list) or not all(isinstance(x, str) for x in value)
               for value in arrays):
            return [], "COMPLETE_EVIDENCE_MISSING_OR_INVALID"
        rows.append({"call": row.get("call"), "role": row.get("role"),
                     "provided": arrays[0], "claimed": arrays[1], "unknown": arrays[2]})
    return rows, None


def _complete_meta(project: Path, call: object) -> dict | None:
    if not isinstance(call, str) or _CALL.fullmatch(call) is None:
        return None
    try:
        path = safe_target(project, (call_folder(project, call) / 'meta.json').relative_to(project).as_posix(), 'call evidence')
        if path.is_symlink() or not path.is_file():
            return None
        value = load_json(path)
    except (OSError, RuntimeError, ValueError, TypeError):
        return None
    if value.get("call") != call or any(name not in value for name in _FIELDS):
        return None
    return value


def _ids(rows: list[dict], field: str) -> list[str]:
    return sorted({value for row in rows for value in row[field]})


def _source_commit(project: Path, evidence: dict) -> str:
    run = evidence.get("run") or {}
    return run.get("contract_commit") or run.get("source_head") or _git(project, "rev-parse", "HEAD").strip()


def _digest(project: Path, evidence: dict) -> tuple[str | None, str | None]:
    run = evidence.get("run") or {}
    if "lessons_digest" in run:
        value = run["lessons_digest"]
        if value is None or isinstance(value, str):
            return value, run.get("lessons_digest_error")
        return None, "LESSONS_DIGEST_INVALID"
    root = project / "openspec/lessons"
    if not (run.get("contract_commit") or run.get("source_head") or root.exists() or root.is_symlink()):
        return None, None
    try:
        return lessons_digest(project, _source_commit(project, evidence)), None
    except Exception as exc:
        return None, "LESSONS_DIGEST_UNAVAILABLE: " + str(exc)
