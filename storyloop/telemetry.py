"""Per-change ledger of agent calls: wall time always, cost and turns when the adapter reports them."""

from __future__ import annotations

from datetime import datetime, timezone
import math
import re
from pathlib import Path
from typing import Callable

from .adapter_profiles import diagnostic_fields
from . import __version__
from .models import require
from .storage import load_json, write_json

Clock = Callable[[], datetime]
PENDING = "pending"  # product calls made before the change id exists
NUMERIC = ("cost_usd", "turns", "duration_ms", "input_tokens", "output_tokens", "segments", "lanes", "perspectives")
STRINGS = ("model", "vendor", "tier_applied")
PROFILE_FIELDS = ("engine_version", "instructions_sha256", "schema_sha256")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


def record_call(project: Path, role: str, change_id: str | None, telemetry: object,
                wall_ms: int, now: Clock | None = None, failed: bool = False,
                call: str | None = None, session: str | None = None,
                engine_fields: dict | None = None) -> dict:
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
        entry.update(diagnostic_fields(telemetry))
        for key in NUMERIC:
            value = telemetry.get(key)
            if (_finite_number(value) and value >= 0):
                entry[key] = value
        for key in STRINGS:
            value = telemetry.get(key)
            if isinstance(value, str) and value:
                entry[key] = value[:120]
        skills = telemetry.get("skills")
        if isinstance(skills, list) and all(_valid_skill(item) for item in skills):
            entry["skills"] = skills
    if isinstance(engine_fields, dict):
        for key in PROFILE_FIELDS[1:]:
            value = engine_fields.get(key)
            if isinstance(value, str) and SHA256.fullmatch(value):
                entry[key] = value
        for key in ("tier_requested", "adapter", "degraded", "subagents_applied", "path_guard"):
            value = engine_fields.get(key)
            if isinstance(value, (str, bool, dict)):
                entry[key] = value
        for key in ('lessons_provided', 'lessons_claimed', 'lessons_unknown'):
            value = engine_fields.get(key)
            if isinstance(value, list) and all(isinstance(item, str) for item in value):
                entry[key] = value[:20]
        if isinstance(engine_fields.get('lessons_truncated'), bool):
            entry['lessons_truncated'] = engine_fields['lessons_truncated']
    entry["engine_version"] = __version__
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
    """Totals per change and role, keeping partial values separate from complete totals."""
    from .telemetry_summary import summarize_entries

    return summarize_entries(_entries(Path(project).resolve(), change_id))


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


def _finite_number(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return not isinstance(value, float) or math.isfinite(value)


def _valid_skill(value: object) -> bool:
    return isinstance(value, dict) and set(value) == {"name", "path", "version", "sha256"} and all(
        isinstance(value[key], str) and len(value[key]) <= 500 for key in value
    )
