"""Human acceptance of manual criteria, bound to the verified implementation commit."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path
from typing import Callable

from .changes import load_change
from .run_guards import base_candidate_matches, manual_criteria, pending_manual_criteria
from .models import StoryLoopError, identifier, require, text  # noqa: F401  # 兼容再导出：保留旧模块导入路径
from .smoke_runner import implementation_digest
from .run_state import save_run
from .storage import load_json, locked, write_json

Clock = Callable[[], datetime]
ACCEPTING_STATES = {"MANUAL_ACCEPTANCE_REQUIRED"}


def load_manual_acceptance(project: Path, change_id: str) -> list[dict]:
    path = _path(Path(project).resolve(), change_id)
    if not path.exists():
        return []
    value = load_json(path)
    require(value.get("version") == 1 and value.get("change_id") == change_id
            and isinstance(value.get("records"), list), "Invalid manual acceptance file")
    return value["records"]


def record_manual_acceptance(
    project: Path, change_id: str, criteria: list[str], note: str, now: Clock | None = None,
) -> dict:
    """Record that a human verified the listed manual criteria on the paused candidate code."""
    project = Path(project).resolve()
    with locked(project):
        return _record_manual_acceptance_locked(project, change_id, criteria, note, now)


def _record_manual_acceptance_locked(
    project: Path, change_id: str, criteria: list[str], note: str, now: Clock | None,
) -> dict:
    change = load_change(project, change_id)
    note = text(note, "acceptance note")
    require(isinstance(criteria, list) and bool(criteria), "Acceptance needs at least one --criterion")
    ids = [identifier(item, "criterion id") for item in criteria]
    manual = manual_criteria(change)
    unknown = [item for item in ids if item not in manual]
    require(not unknown, f"Not manual acceptance criteria of this change: {', '.join(unknown)}")
    run_path = project / ".storyloop/state/runs" / f"{change_id}.json"
    require(run_path.exists(), "Run the change until it is verified before accepting manual criteria")
    state = load_json(run_path)
    require(state.get("status") in ACCEPTING_STATES,
            "Manual acceptance is only possible while the run waits for it (MANUAL_ACCEPTANCE_REQUIRED)")
    commit = state.get("candidate_sha256")
    require(isinstance(commit, str) and bool(commit), "Paused run lacks its candidate digest")
    candidate_matches = (base_candidate_matches(project, state)
                         if change["product"].get("nature") == "base"
                         else implementation_digest(project) == commit)
    require(candidate_matches,
            "Workspace differs from the verified candidate; use `storyloop reset` and run again before accepting")
    moment = (now or _utc_now)().astimezone(timezone.utc).isoformat(timespec="seconds")
    records = [record for record in load_manual_acceptance(project, change_id)
               if not (record["criterion_id"] in ids and record.get("candidate_sha256") == commit)]
    records.extend({"criterion_id": item, "note": note, "accepted_at": moment,
                    "candidate_sha256": commit} for item in ids)
    acceptance_path = _path(project, change_id)
    write_json(acceptance_path, {"version": 1, "change_id": change_id, "records": records},
               max_bytes=1_000_000)
    if change["product"].get("nature") == "base":
        state["manual_acceptance_sha256"] = hashlib.sha256(acceptance_path.read_bytes()).hexdigest()
        save_run(project, state)
    pending = pending_manual_criteria(change, records, commit)
    return {"status": "ACCEPTED" if not pending else "MANUAL_ACCEPTANCE_REQUIRED",
            "change_id": change_id, "accepted": ids, "pending": pending,
            "next": None if not pending else "storyloop run <project> --change " + change_id}


def _path(project: Path, change_id: str) -> Path:
    return project / ".storyloop/state/acceptance" / f"{change_id}.json"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
