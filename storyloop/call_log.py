"""Per-call records of agent invocations, kept for diagnosis (never evidence).

Each call gets `.storyloop/state/telemetry/calls/<call-id>/` with the redacted request, the
role's answer (or the raw text that failed to parse), the adapter's full stderr and a
`meta.json` naming the outcome and the agent session ids, so a failed call can be read after
the fact or resumed in its own CLI (`codex resume <id>`, `claude --resume <id>`). The folder
lives under the telemetry ledger, which the engine already tolerates changing while an agent
runs; agents with write access could still edit it, which is why nothing here is a verdict.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import secrets

from .models import StoryLoopError
from .processutil import redact
from .storage import write_json, write_text_atomic

CALLS_ROOT = Path(".storyloop/state/telemetry/calls")
MAX_TEXT = 4_000_000


def open_call(project: Path, role: str, ledger: str | None, adapter: object, request: dict) -> str:
    """Create the call folder with its request; returns the call id (sortable by start time)."""
    moment = datetime.now(timezone.utc)
    call_id = f"{moment.strftime('%Y%m%dT%H%M%S')}-{role}-{secrets.token_hex(3)}"
    folder = Path(project) / CALLS_ROOT / call_id
    write_json(folder / "request.json", _redacted(request))
    write_json(folder / "meta.json", {"version": 1, "call": call_id, "role": role, "ledger": ledger,
                                      "adapter": adapter if isinstance(adapter, str) else None,
                                      "started_at": moment.isoformat(timespec="seconds"),
                                      "status": "RUNNING"})
    return call_id


def close_call(project: Path, call_id: str, *, status: str, wall_ms: int, response: object = None,
               raw: str | None = None, stderr: str = "", error: str | None = None,
               code: str | None = None, sessions: list[str] | None = None,
               schema_errors: list[str] | None = None, repairs: int = 0,
               semantic_repair: bool = False) -> None:
    """Finish the record; failures to write it never mask the call's own outcome."""
    folder = Path(project) / CALLS_ROOT / call_id
    try:
        if isinstance(response, dict):
            write_json(folder / "response.json", _redacted(response))
        elif raw:
            write_text_atomic(folder / "response.txt", redact(raw[-MAX_TEXT:]))
        if stderr:
            write_text_atomic(folder / "stderr.log", redact(stderr[-MAX_TEXT:]))
        meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
        meta.update({"status": status, "wall_ms": int(wall_ms), "sessions": list(sessions or []),
                     "repairs": repairs})
        if semantic_repair:
            meta["semantic_repair"] = True
        if error:
            meta["error"] = redact(error)[:4000]
        if code:
            meta["code"] = code
        if schema_errors:
            meta["schema_errors"] = list(schema_errors)
        write_json(folder / "meta.json", meta)
    except (OSError, ValueError, StoryLoopError):
        pass


def call_folder(project: Path, call_id: str) -> Path:
    return Path(project) / CALLS_ROOT / call_id


def load_meta(project: Path, call_id: str) -> dict | None:
    try:
        value = json.loads((call_folder(project, call_id) / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _redacted(value: object) -> object:
    """Redact every string in a JSON value (text-level redaction would break the JSON)."""
    if isinstance(value, dict):
        return {key: _redacted(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redacted(item) for item in value]
    return redact(value) if isinstance(value, str) else value


def record_context(project: Path, call_id: str, context: dict) -> None:
    """Attach a provenance-only context manifest captured before adapter invocation."""
    path = call_folder(project, call_id) / 'meta.json'
    meta = json.loads(path.read_text(encoding='utf-8'))
    meta['context'] = _redacted(context)
    write_json(path, meta)


def record_lessons_skipped(project: Path, call_id: str, reason: str) -> None:
    """Record why the entire lesson layer was omitted."""
    record_lesson_evidence(project, call_id, {'lessons_skipped': reason})


def record_lesson_evidence(project: Path, call_id: str, evidence: dict) -> None:
    """Preserve complete engine-owned evidence; ledger arrays are bounded projections."""
    path = call_folder(project, call_id) / 'meta.json'
    meta = json.loads(path.read_text(encoding='utf-8'))
    meta.update(_redacted(evidence))
    write_json(path, meta)
