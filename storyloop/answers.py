"""Human answers to agent questions, recorded per attempt and forwarded to later attempts."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .models import identifier, require, text
from .storage import load_json, write_json

Clock = Callable[[], datetime]


def load_answers(project: Path, change_id: str) -> list[dict]:
    path = _path(Path(project).resolve(), change_id)
    if not path.exists():
        return []
    value = load_json(path)
    require(value.get("version") == 1 and value.get("change_id") == change_id
            and isinstance(value.get("entries"), list), "Invalid answers file")
    return value["entries"]


def pending_questions(project: Path, change_id: str) -> list[str]:
    """Questions of the current NEEDS_INPUT pause that have no answer yet."""
    project = Path(project).resolve()
    state = _run_state(project, change_id)
    if not state or state.get("status") != "NEEDS_INPUT":
        return []
    return _pending(state, load_answers(project, change_id))


def record_answers(project: Path, change_id: str, answers: list[str], now: Clock | None = None) -> dict:
    """Answer the pending questions in order; one answer may apply to every question."""
    project = Path(project).resolve()
    change_id = identifier(change_id, "change id")
    state = _run_state(project, change_id)
    require(state is not None and state.get("status") == "NEEDS_INPUT",
            "There are no pending questions: the run is not waiting for input")
    entries = load_answers(project, change_id)
    pending = _pending(state, entries)
    require(bool(pending), "Every pending question is already answered; run the change")
    require(isinstance(answers, list) and bool(answers), "At least one answer is required")
    answers = [text(item, "answer") for item in answers]
    if len(answers) == 1 and len(pending) > 1:
        answers = answers * len(pending)
    require(len(answers) == len(pending),
            f"Expected {len(pending)} answers for {len(pending)} pending questions, got {len(answers)}")
    moment = (now or _utc_now)().astimezone(timezone.utc).isoformat(timespec="seconds")
    attempt = int(state.get("attempt", 0))
    entries.extend({"attempt": attempt, "question": question, "answer": answer, "answered_at": moment}
                   for question, answer in zip(pending, answers))
    write_json(_path(project, change_id), {"version": 1, "change_id": change_id, "entries": entries},
               max_bytes=2_000_000)
    return {"status": "ANSWERED", "change_id": change_id, "answered": len(pending),
            "pending": [], "next": f"storyloop run <project> --change {change_id}"}


def _pending(state: dict, entries: list[dict]) -> list[str]:
    attempt = int(state.get("attempt", 0))
    answered = {item["question"] for item in entries if int(item.get("attempt", -1)) == attempt}
    questions = state.get("questions") or []
    return [question for question in questions if question not in answered]


def _run_state(project: Path, change_id: str) -> dict | None:
    path = project / ".storyloop/state/runs" / f"{change_id}.json"
    return load_json(path) if path.exists() else None


def _path(project: Path, change_id: str) -> Path:
    return project / ".storyloop/state/answers" / f"{change_id}.json"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
