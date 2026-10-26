"""Bounded, read-only normalization of persisted StoryLoop change evidence."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

from .path_security import safe_target
from .models import StoryLoopError
from .metrics_compute_support import _add_missing
from .report_evidence import IDENTIFIER, MAX_LEDGER_BYTES, _read_object
from .stop_report import STOP_STATUSES


MAX_CHANGE_BYTES = 2_000_000
MAX_RUN_BYTES = 8_000_000
MAX_REPORT_BYTES = 8_000_000
MAX_AUX_BYTES = 2_000_000
MISSING = object()


def collect(project: Path, since: date | None = None) -> dict:
    """Collect persisted metrics inputs without modifying or executing the project."""
    root = Path(project).resolve()
    missing: list[dict] = []
    discrepancies: list[dict] = []
    records = []
    for change_id in _change_ids(root):
        change = _load_state(root, _change_path(root, change_id), change_id, MAX_CHANGE_BYTES, missing)
        run = _load_state(root, _run_path(root, change_id), change_id, MAX_RUN_BYTES, missing)
        report = _load_report(root, change_id, missing)
        record = _record(root, change_id, change, run, report, missing, discrepancies)
        approved = _as_date(record["approved_at"])
        if since is not None and approved is None:
            _add_missing(missing, change_id, "UNREADABLE")
        elif since is None or approved >= since:
            records.append(record)
    records.sort(key=lambda item: (_date_sort_key(item["approved_at"]), item["change_id"]))
    missing.sort(key=lambda item: (item["change_id"], item["reason"]))
    discrepancies.sort(key=lambda item: (item["change_id"], item["field"]))
    return {"changes": records, "missing": missing, "discrepancies": discrepancies}


def _change_ids(root: Path) -> list[str]:
    identifiers = set(_file_ids(root, root / ".storyloop/state/changes"))
    identifiers.update(_file_ids(root, root / ".storyloop/state/runs"))
    reports = root / "reports/storyloop"
    if _safe_directory(root, reports):
        try:
            for child in reports.iterdir():
                if not IDENTIFIER.fullmatch(child.name):
                    continue
                if child.is_symlink():
                    identifiers.add(child.name)
                elif child.is_dir() and _present(child / "report.json"):
                    identifiers.add(child.name)
        except OSError:
            pass
    return sorted(identifiers)


def _file_ids(root: Path, directory: Path) -> list[str]:
    if not _safe_directory(root, directory):
        return []
    identifiers = []
    try:
        for path in directory.iterdir():
            if path.suffix == ".json" and IDENTIFIER.fullmatch(path.stem):
                identifiers.append(path.stem)
    except OSError:
        return []
    return identifiers


def _safe_directory(root: Path, path: Path) -> bool:
    try:
        return (not path.is_symlink() and path.is_dir()
                and path.resolve().is_relative_to(root.resolve()))
    except OSError:
        return False


def _load_state(root: Path, path: Path, change_id: str, limit: int,
                missing: list[dict]) -> dict | None:
    if not _present(path):
        return None
    value = _read_object(root, path, limit)
    if value is None or value.get("version") != 1 or value.get("change_id") != change_id:
        _add_missing(missing, change_id, "UNREADABLE")
        return None
    return value


def _load_report(root: Path, change_id: str, missing: list[dict]) -> dict | None:
    path = root / "reports/storyloop" / change_id / "report.json"
    if not _present(path):
        return None
    value = _read_object(root, path, MAX_REPORT_BYTES)
    if value is None or value.get("change_id") != change_id:
        _add_missing(missing, change_id, "UNREADABLE")
        return None
    if value.get("version") != 1:
        _add_missing(missing, change_id, "UNKNOWN_REPORT_VERSION")
        return None
    return value


def _record(root: Path, change_id: str, change: dict | None, run: dict | None,
            report: dict | None, missing: list[dict], discrepancies: list[dict]) -> dict:
    gaps: dict[str, str] = {}
    record = {"change_id": change_id, "has_report": report is not None, "gaps": gaps}
    _identity_fields(record, gaps, change, run, report)
    _preferred_fields(record, gaps, change_id, report, run, discrepancies)
    record["base_review_mode"] = (_string(_nested(report, "review_mode"))
                                  or _string(_nested(run, "base_review", "mode")))
    _run_fields(record, gaps, root, change_id, run, missing)
    _link_fields(record, change, run, report)
    _commit_fields(record, gaps, change_id, report, run, discrepancies)
    record["ledger"] = _ledger(root, change_id)
    if record["ledger"] is None:
        gaps["ledger"] = "UNREADABLE"
        if _present(root / ".storyloop/state/telemetry" / f"{change_id}.json"):
            _add_missing(missing, change_id, "UNREADABLE")
    record["signal_evidence"] = _signal_evidence(change, report)
    return record


def _identity_fields(record: dict, gaps: dict, change: dict | None, run: dict | None,
                     report: dict | None) -> None:
    product = _mapping(change, "product")
    nature = product.get("nature", "feature") if product is not None else "feature"
    record["nature"] = nature if isinstance(nature, str) else "feature"
    approved_at = _first(
        _nested(change, "approval", "approved_at"),
        _nested(report, "requirement", "approval", "approved_at"),
        _nested(run, "started_at"),
    )
    _put(record, gaps, "approved_at", approved_at)


def _preferred_fields(record: dict, gaps: dict, change_id: str, report: dict | None,
                      run: dict | None, discrepancies: list[dict]) -> None:
    pairs = {
        "status": (_string(_nested(report, "status")), _string(_nested(run, "status"))),
        "failures": (_list(_nested(report, "correction", "failures")),
                     _run_failures(run)),
        "overrules": (_list(_nested(report, "correction", "overrules")),
                      _list(_nested(run, "overrules"))),
        "attempts": (_integer(_nested(report, "telemetry", "attempts")),
                     _integer(_nested(run, "attempt"))),
        "by_role": (_dict(_nested(report, "telemetry", "by_role")),
                    _dict(_nested(run, "telemetry", "by_role"))),
    }
    pairs.update(_routing_pairs(report, run))
    pairs.update(_decision_pairs(report, run))
    for field, (report_value, run_value) in pairs.items():
        reason = "NO_ROUTING" if field in {"tier_initial", "tier_effective", "routing_history"} else "UNREADABLE"
        _put(record, gaps, field, _prefer(
            change_id, field, report_value, run_value, discrepancies), reason)
    record["review_stop_findings"] = _review_stop_findings(report)
    if record["review_stop_findings"] is None:
        gaps["review_stop_findings"] = "UNREADABLE"
    stops = _stops(run, record["failures"])
    _put(record, gaps, "stops", stops)


def _routing_pairs(report: dict | None, run: dict | None) -> dict:
    return {
        "tier_initial": (_string(_nested(report, "routing", "initial")),
                         _string(_nested(run, "routing", "initial_tier"))),
        "tier_effective": (_string(_nested(report, "routing", "effective")),
                           _string(_nested(run, "routing", "effective_tier"))),
        "routing_history": (_list(_nested(report, "routing", "history")),
                            _list(_nested(run, "routing", "history"))),
    }


def _decision_pairs(report: dict | None, run: dict | None) -> dict:
    report_decisions = _mapping(_mapping(report, "routing"), "manual_decisions")
    planning = _mapping(run, "planning")
    plan = _mapping(planning, "plan")
    return {
        "continue_heavy": (_decision(report_decisions, "continue_heavy"),
                           _decision(planning, "continue_heavy")),
        "design_approved": (_decision(report_decisions, "design"),
                            _decision(plan, "approval") if planning is not None else MISSING),
    }


def _run_fields(record: dict, gaps: dict, root: Path, change_id: str,
                run: dict | None, missing: list[dict]) -> None:
    for field in ("agent_failures",):
        _put(record, gaps, field, _list(_nested(run, field)))
    for field in ("resets", "repairs"):
        values = _list(_nested(run, field))
        _put(record, gaps, field, len(values) if values is not MISSING else MISSING)
    answers_path = root / ".storyloop/state/answers" / f"{change_id}.json"
    manual_path = root / ".storyloop/state/acceptance" / f"{change_id}.json"
    answers = _aux_list(root, answers_path, change_id, "entries")
    manual = _aux_list(root, manual_path, change_id, "records")
    if answers is MISSING and _present(answers_path):
        _add_missing(missing, change_id, "UNREADABLE")
    if manual is MISSING and _present(manual_path):
        _add_missing(missing, change_id, "UNREADABLE")
    _put(record, gaps, "answers", len(answers) if answers is not MISSING else MISSING)
    _put(record, gaps, "manual_acceptance", len(manual) if manual is not MISSING else MISSING)


def _link_fields(record: dict, change: dict | None, run: dict | None,
                 report: dict | None) -> None:
    report_supersedes = _string(_nested(report, "correction", "supersedes"))
    change_supersedes = _string(_nested(change, "supersedes", "change_id"))
    record["supersedes"] = _none_if_missing(_first(report_supersedes, change_supersedes))
    record["superseded_by"] = _none_if_missing(_first(
        _string(_nested(run, "superseded_by")), _string(_nested(change, "superseded_by"))))
    nature = record["nature"]
    origin = _string(_nested(change, "product", "defect", "origin_change"))
    record["origin_change"] = _none_if_missing(origin) if nature == "fix" else None


def _commit_fields(record: dict, gaps: dict, change_id: str, report: dict | None,
                   run: dict | None, discrepancies: list[dict]) -> None:
    commits = {}
    sources = {
        "source_head": (MISSING, _string(_nested(run, "source_head"))),
        "contract_commit": (MISSING, _string(_nested(run, "contract_commit"))),
        "implementation_commit": (_string(_nested(report, "implementation_commit")),
                                  _string(_nested(run, "implementation_commit"))),
        "merge_commit": (_string(_nested(report, "merge_commit")),
                         _string(_nested(run, "merge_commit"))),
    }
    for field, (report_value, run_value) in sources.items():
        value = _prefer(change_id, field, report_value, run_value, discrepancies)
        commits[field] = _none_if_missing(value)
        if value is MISSING:
            gaps[f"commits.{field}"] = "UNREADABLE"
    record["commits"] = commits


def _aux_list(root: Path, path: Path, change_id: str, field: str) -> list | object:
    if not _present(path):
        return MISSING
    value = _read_object(root, path, MAX_AUX_BYTES)
    if value is None or value.get("version") != 1 or value.get("change_id") != change_id:
        return MISSING
    return _list(value.get(field, MISSING))


def _ledger(root: Path, change_id: str) -> list | None:
    path = root / ".storyloop/state/telemetry" / f"{change_id}.json"
    try:
        safe_target(root, path.relative_to(root).as_posix(), "telemetry path")
        path.stat()
    except FileNotFoundError:
        return []
    except (OSError, StoryLoopError):
        return None
    value = _read_object(root, path, MAX_LEDGER_BYTES)
    if (value is None or value.get("version") != 1 or value.get("change_id") != change_id
            or not isinstance(value.get("entries"), list)):
        return None
    return value["entries"]


def _signal_evidence(change: dict | None, report: dict | None) -> dict:
    evidence = {}
    for name, value in (
        ("stories", _nested(change, "product", "stories")),
        ("layers", _nested(report, "layers")),
        ("traceability", _nested(report, "traceability")),
    ):
        if value is not MISSING and value is not None:
            evidence[name] = value
    return evidence


def _review_stop_findings(report: dict | None) -> int | None:
    findings = _list(_nested(report, "review", "findings"))
    if findings is MISSING:
        return None
    return sum(isinstance(item, dict) and item.get("target") in {"oracle", "contract"}
               for item in findings)


def _run_failures(run: dict | None) -> list | object:
    failures = _list(_nested(run, "failures"))
    if failures is MISSING:
        return MISSING
    try:
        from .change_report import correction_view
        return correction_view({"failures": failures}, {})["failures"]
    except (AttributeError, KeyError, TypeError):
        return MISSING


def _stops(run: dict | None, failures: object) -> list[str] | object:
    values = set()
    status = _string(_nested(run, "status"))
    stop_status = _string(_nested(run, "stop", "status"))
    for value in (status, stop_status):
        if value is not MISSING and value in STOP_STATUSES:
            values.add(value)
    if isinstance(failures, list):
        if any(isinstance(item, dict) and item.get("attribution") == "contract"
               for item in failures):
            values.add("NEEDS_SPEC_FIX")
        return sorted(values)
    return sorted(values) if run is not None else MISSING


def _prefer(change_id: str, field: str, report: object, run: object,
            discrepancies: list[dict]) -> object:
    if report is not MISSING and run is not MISSING and report != run:
        discrepancies.append({"change_id": change_id, "field": field,
                              "report": report, "run": run})
    return report if report is not MISSING else run


def _put(record: dict, gaps: dict, field: str, value: object,
         reason: str = "UNREADABLE") -> None:
    record[field] = _none_if_missing(value)
    if value is MISSING:
        gaps[field] = reason


def _nested(value: object, *keys: str) -> object:
    current = value
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return MISSING
        current = current[key]
    return MISSING if current is None else current


def _mapping(value: object, key: str) -> dict | None:
    candidate = value.get(key) if isinstance(value, dict) else None
    return candidate if isinstance(candidate, dict) else None


def _first(*values: object) -> object:
    return next((value for value in values if value is not MISSING), MISSING)


def _string(value: object) -> str | object:
    return value if isinstance(value, str) else MISSING


def _integer(value: object) -> int | object:
    return value if type(value) is int and value >= 0 else MISSING


def _list(value: object) -> list | object:
    return value if isinstance(value, list) else MISSING


def _dict(value: object) -> dict | object:
    return value if isinstance(value, dict) else MISSING


def _decision(container: dict | None, key: str) -> bool | object:
    if container is None or key not in container:
        return MISSING
    value = container[key]
    return False if value is None else True if isinstance(value, dict) else MISSING


def _none_if_missing(value: object) -> Any:
    return None if value is MISSING else value


def _present(path: Path) -> bool:
    try:
        return path.exists() or path.is_symlink()
    except OSError:
        return False


def _as_date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _date_sort_key(value: object) -> tuple[bool, str]:
    return (not isinstance(value, str), value if isinstance(value, str) else "")


def _change_path(root: Path, change_id: str) -> Path:
    return root / ".storyloop/state/changes" / f"{change_id}.json"


def _run_path(root: Path, change_id: str) -> Path:
    return root / ".storyloop/state/runs" / f"{change_id}.json"
