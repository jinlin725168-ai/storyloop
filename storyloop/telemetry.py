"""Per-change ledger of agent calls: wall time always, cost and turns when the adapter reports them."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .models import require
from .storage import load_json, write_json

Clock = Callable[[], datetime]
PENDING = "pending"  # product calls made before the change id exists
NUMERIC = ("cost_usd", "turns", "duration_ms", "input_tokens", "output_tokens", "segments", "lanes", "perspectives")


def record_call(project: Path, role: str, change_id: str | None, telemetry: object,
                wall_ms: int, now: Clock | None = None, failed: bool = False,
                call: str | None = None, session: str | None = None) -> dict:
    """Append one agent call; adapter telemetry is optional and only known keys are kept.

    `call` names the diagnostic record under calls/ and `session` the agent CLI session ids
    (comma-separated), so a ledger line leads straight to what the agent saw and said.
    """
    project = Path(project).resolve()
    entry = {"role": role, "wall_ms": int(wall_ms),
             "at": (now or _utc_now)().astimezone(timezone.utc).isoformat(timespec="seconds")}
    if failed:
        entry["failed"] = True
    if call:
        entry["call"] = call
    if session:
        entry["session"] = session[:400]
    if isinstance(telemetry, dict):
        for key in NUMERIC:
            value = telemetry.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
                entry[key] = value
        if isinstance(telemetry.get("model"), str) and telemetry["model"]:
            entry["model"] = telemetry["model"][:120]
    key = change_id or PENDING
    entries = _entries(project, key)
    entries.append(entry)
    _write(project, key, entries)
    return entry


def adopt_pending(project: Path, change_id: str) -> int:
    """Move calls recorded before the change existed (product co-creation) under its id."""
    project = Path(project).resolve()
    pending = _entries(project, PENDING)
    if not pending:
        return 0
    entries = _entries(project, change_id)
    entries.extend(pending)
    _write(project, change_id, entries)
    _write(project, PENDING, [])
    return len(pending)


def summarize(project: Path, change_id: str) -> dict:
    """Totals per change and per role; cost is None when no call reported one."""
    entries = _entries(Path(project).resolve(), change_id)
    by_role: dict[str, dict] = {}
    for entry in entries:
        role = by_role.setdefault(entry["role"], {"calls": 0, "failed": 0, "agent_ms": 0,
                                                  "cost_usd": None, "turns": 0})
        role["calls"] += 1
        role["failed"] += 1 if entry.get("failed") else 0
        role["agent_ms"] += int(entry.get("wall_ms", 0))
        role["turns"] += int(entry.get("turns", 0))
        if "cost_usd" in entry:
            role["cost_usd"] = round((role["cost_usd"] or 0.0) + float(entry["cost_usd"]), 4)
    costs = [role["cost_usd"] for role in by_role.values() if role["cost_usd"] is not None]
    return {"calls": len(entries), "failed": sum(1 for e in entries if e.get("failed")),
            "agent_ms": sum(int(e.get("wall_ms", 0)) for e in entries),
            "cost_usd": round(sum(costs), 4) if costs else None, "by_role": by_role}


def ledger_entries(project: Path, change_id: str) -> list[dict]:
    """The raw ledger of one change, oldest call first."""
    return list(_entries(Path(project).resolve(), change_id))


def _entries(project: Path, key: str) -> list[dict]:
    path = _path(project, key)
    if not path.exists():
        return []
    value = load_json(path)
    require(value.get("version") == 1 and isinstance(value.get("entries"), list), "Invalid telemetry file")
    return value["entries"]


def _write(project: Path, key: str, entries: list[dict]) -> None:
    write_json(_path(project, key), {"version": 1, "change_id": key, "entries": entries},
               max_bytes=4_000_000)


def _path(project: Path, key: str) -> Path:
    return project / ".storyloop/state/telemetry" / f"{key}.json"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
