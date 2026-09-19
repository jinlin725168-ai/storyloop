"""Human acceptance of manual criteria, bound to the verified implementation commit."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .changes import load_change
from .models import StoryLoopError, identifier, require, text
from .smoke_runner import git_visible_digest
from .storage import load_json, write_json

Clock = Callable[[], datetime]
ACCEPTING_STATES = {"MANUAL_ACCEPTANCE_REQUIRED"}


def manual_criteria(change: dict) -> list[str]:
    return [criterion["id"] for story in change["product"]["stories"]
            for criterion in story["acceptance"] if criterion["kind"] == "manual"]


def load_manual_acceptance(project: Path, change_id: str) -> list[dict]:
    path = _path(Path(project).resolve(), change_id)
    if not path.exists():
        return []
    value = load_json(path)
    require(value.get("version") == 1 and value.get("change_id") == change_id
            and isinstance(value.get("records"), list), "Invalid manual acceptance file")
    return value["records"]


def pending_manual_criteria(change: dict, records: list[dict], candidate_sha256: str) -> list[str]:
    """Manual criteria without an acceptance bound to exactly this Git-visible code state."""
    accepted = {record["criterion_id"] for record in records
                if record.get("candidate_sha256") == candidate_sha256}
    return [criterion for criterion in manual_criteria(change) if criterion not in accepted]


def record_manual_acceptance(
    project: Path, change_id: str, criteria: list[str], note: str, now: Clock | None = None,
) -> dict:
    """Record that a human verified the listed manual criteria on the paused candidate code."""
    project = Path(project).resolve()
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
    require(git_visible_digest(project) == commit,
            "Workspace differs from the verified candidate; use `storyloop reset` and run again before accepting")
    moment = (now or _utc_now)().astimezone(timezone.utc).isoformat(timespec="seconds")
    records = [record for record in load_manual_acceptance(project, change_id)
               if not (record["criterion_id"] in ids and record.get("candidate_sha256") == commit)]
    records.extend({"criterion_id": item, "note": note, "accepted_at": moment,
                    "candidate_sha256": commit} for item in ids)
    write_json(_path(project, change_id), {"version": 1, "change_id": change_id, "records": records},
               max_bytes=1_000_000)
    pending = pending_manual_criteria(change, records, commit)
    return {"status": "ACCEPTED" if not pending else "MANUAL_ACCEPTANCE_REQUIRED",
            "change_id": change_id, "accepted": ids, "pending": pending,
            "next": None if not pending else "storyloop run <project> --change " + change_id}


def _path(project: Path, change_id: str) -> Path:
    return project / ".storyloop/state/acceptance" / f"{change_id}.json"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
