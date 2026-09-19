"""Explicit human recovery of a stuck product run, with an audit trail."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .changes import load_change
from .models import identifier, require, text
from .project_git import current_branch
from .storage import load_json, write_json

Clock = Callable[[], datetime]
# Terminal or wedged states a human may reset. Everything else either resumes by itself
# (`storyloop run`) or must go through its own command (accept, answer, base-update).
RESETTABLE = {"WORKSPACE_TAINTED", "MANUAL_ACCEPTANCE_REQUIRED"}
STALE_KEYS = ("error", "questions", "pending", "verified", "candidate_sha256", "next")


def reset_run(project: Path, change_id: str, reason: str, now: Clock | None = None) -> dict:
    """Return a wedged run to IMPLEMENTING so the next `run` retries the interrupted attempt.

    Nothing in the working tree is touched; only the run state changes. The interrupted attempt
    is not charged against the budget, and the reset itself is recorded with its reason.
    """
    project = Path(project).resolve()
    change_id = identifier(change_id, "change id")
    reason = text(reason, "reset reason")
    change = load_change(project, change_id)
    path = project / ".storyloop/state/runs" / f"{change_id}.json"
    require(path.exists(), "There is no run to reset for this change")
    state = load_json(path)
    require(state.get("change_id") == change_id, "Run state belongs to another change")
    require(state.get("status") in RESETTABLE,
            f"Only {', '.join(sorted(RESETTABLE))} runs can be reset (current: {state.get('status')})")
    require(state.get("contract_digest") == (change.get("approval") or {}).get("contract_digest"),
            "Run state belongs to a different approved contract; approve and run again instead")
    require(current_branch(project) == state.get("feature_branch"),
            "Check out the run's feature branch before resetting")
    moment = (now or _utc_now)().astimezone(timezone.utc).isoformat(timespec="seconds")
    record = {"at": moment, "from_status": state["status"], "attempt": int(state.get("attempt", 0)),
              "reason": reason}
    for key in STALE_KEYS:
        state.pop(key, None)
    state["attempt"] = max(int(state.get("attempt", 0)) - 1, 0)
    state["status"] = "IMPLEMENTING"
    state.setdefault("resets", []).append(record)
    write_json(path, state, max_bytes=8_000_000)
    return {"status": "IMPLEMENTING", "change_id": change_id, "reset": record,
            "next": f"storyloop run <project> --change {change_id}"}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
