"""Small persisted-state fixtures for metrics collection tests."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile


UNSET = object()


def metrics_project() -> Path:
    return Path(tempfile.mkdtemp(prefix="storyloop-metrics-"))


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def write_change(
    project: Path, change_id: str, *, approved_at: str = "2026-10-10T00:00:00+00:00",
    nature: str | None = None, origin_change: str | None = None,
    stories: list[dict] | None = None,
) -> None:
    product: dict = {}
    if nature is not None:
        product["nature"] = nature
    if origin_change is not None:
        product["defect"] = {"origin_change": origin_change}
    if stories is not None:
        product["stories"] = stories
    _write(project / ".storyloop/state/changes" / f"{change_id}.json", {
        "version": 1, "change_id": change_id, "product": product,
        "approval": {"approved_at": approved_at},
    })


def write_run(
    project: Path, change_id: str, *, status: str = "PASSED", attempt: int = 1,
    failures: object = None, agent_failures: object = UNSET,
    started_at: str = "2026-10-10T01:00:00+00:00", **extra: object,
) -> None:
    value: dict = {
        "version": 1, "change_id": change_id, "status": status,
        "attempt": attempt, "started_at": started_at,
    }
    if failures is not UNSET:
        value["failures"] = [] if failures is None else failures
    if agent_failures is not UNSET:
        value["agent_failures"] = agent_failures
    value.update(extra)
    _write(project / ".storyloop/state/runs" / f"{change_id}.json", value)


def write_report(
    project: Path, change_id: str, *, version: int = 1, status: str = "PASSED",
    attempts: object = 1, approved_at: str = "2026-10-10T00:00:00+00:00",
    failures: object = None, overrules: object = None,
    routing: object = UNSET, review: object = UNSET, **extra: object,
) -> None:
    if routing is UNSET:
        routing = {
            "initial": "standard", "effective": "standard", "history": [],
            "manual_decisions": {"design": None, "continue_heavy": None},
        }
    correction: dict = {"supersedes": None}
    if failures is not UNSET:
        correction["failures"] = [] if failures is None else failures
    if overrules is not UNSET:
        correction["overrules"] = [] if overrules is None else overrules
    value = {
        "version": version, "change_id": change_id, "status": status,
        "requirement": {"approval": {"approved_at": approved_at}},
        "routing": routing, "correction": correction,
        "telemetry": {"attempts": attempts, "by_role": {}},
        "review": {"findings": []} if review is UNSET else review,
    }
    value.update(extra)
    _write(project / "reports/storyloop" / change_id / "report.json", value)


def write_answers(project: Path, change_id: str, entries: list[dict]) -> None:
    _write(project / ".storyloop/state/answers" / f"{change_id}.json",
           {"version": 1, "change_id": change_id, "entries": entries})


def write_manual_acceptance(project: Path, change_id: str, records: list[dict]) -> None:
    _write(project / ".storyloop/state/acceptance" / f"{change_id}.json",
           {"version": 1, "change_id": change_id, "records": records})


def write_ledger(project: Path, change_id: str, entries: list[dict]) -> None:
    _write(project / ".storyloop/state/telemetry" / f"{change_id}.json",
           {"version": 1, "change_id": change_id, "entries": entries})


def link_report_outside_project(project: Path, change_id: str) -> Path:
    outside = Path(tempfile.mkdtemp(prefix="storyloop-metrics-outside-"))
    target = outside / "report.json"
    _write(target, {"version": 1, "change_id": change_id, "status": "PASSED"})
    report = project / "reports/storyloop" / change_id / "report.json"
    report.parent.mkdir(parents=True)
    report.symlink_to(target)
    return outside
