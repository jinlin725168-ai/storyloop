"""Business context, ledger and diagnostics for project agent calls."""
from __future__ import annotations

from pathlib import Path
import hashlib
import json
import time

from .adapter_profiles import model_for, sequential_profile, sequential_instructions
from .agent_transport import invoke as invoke_transport, conform, _bounded_json  # noqa: F401  # 兼容再导出：保留旧模块导入路径
from .baseline import load_project_config
from .models import StoryLoopError, require
from .foundation_survey import foundation_payload
from .skills import selected_snapshots
from .call_log import close_call, open_call
from .role_schemas import schema_for, wire_schema
from .role_instructions import FOUNDATION_NOTE, INSTRUCTIONS  # noqa: F401  # 兼容再导出：保留旧模块导入路径
from .telemetry import record_call
from .role_profiles import build_profile, resolve_adapters

ROLES = {"product", "acceptance", "implement", "review", "survey", "characterize", "design"}

FOUNDATION_LEDGER = "foundation"  # survey calls are base work, billed apart from any change

REPAIR_NOTE = (
    "\n\nWhen payload.validation_errors is present, your previous answer "
    "(payload.previous_response) passed the response schema but failed the engine's checks "
    "with those errors: return a complete corrected answer that fixes exactly them and "
    "changes nothing else."
)


def instructions_for(project: Path, role: str) -> str:
    """Role instructions with the role's packaged skills embedded (project copy first)."""
    return _instruction_bundle(project, role)[0]


def _instruction_bundle(project: Path, role: str, stacks: list[str] | None = None) -> tuple[str, list[dict]]:
    if stacks is None and (project / '.storyloop/config.json').is_file():
        stacks = load_project_config(project).get('profile', {}).get('stacks', [])
    text_ = INSTRUCTIONS[role] + REPAIR_NOTE
    snapshots = []
    for skill, snapshot in selected_snapshots(project, role, stacks or []):
        name = snapshot["name"]
        text_ += f"\n\nSkill {name}:\n" + skill
        snapshots.append(snapshot)
    return text_, snapshots


def invoke_project_agent(project: Path, role: str, payload: dict) -> dict:
    """Invoke one stateless agent using the repository-local adapter configuration.

    The role's JSON Schema travels with the request (`response_schema`) so adapters can have
    their CLI enforce it natively; the engine checks the answer against it either way. Every
    call leaves a diagnostic record (request, answer, stderr, session ids) under the telemetry
    ledger, named by the `call` id in the ledger entry.
    """
    project = Path(project).resolve()
    require(role in ROLES, "Unknown project agent role")
    require(isinstance(payload, dict), "Agent payload must be an object")
    config = load_project_config(project)
    from .cost_limits import check_before_call
    from .cross_vendor import invoke
    from .role_profiles import enabled
    if enabled(config):
        from .role_subagents import routing_tier
        payload = {**payload, 'routing_tier': routing_tier(project, payload)}
    ledger = FOUNDATION_LEDGER if role == "survey" else _change_id(payload)
    check_before_call(project, ledger, config)
    return invoke(project, role, payload, config, ledger,
                  lambda settings, degraded: _invoke_once(project, role, payload, config, settings, degraded))


def _invoke_once(project, role, payload, config, settings, degraded):
    from .cost_limits import check_before_call
    ledger = FOUNDATION_LEDGER if role == "survey" else _change_id(payload)
    check_before_call(project, ledger, config)
    adapter_name = settings["adapter"]
    adapter_config = {**config, **resolve_adapters(config)[adapter_name]}
    foundation = foundation_payload(project) if role != "survey" else None
    if foundation and "foundation" not in payload:
        payload = {**payload, "foundation": foundation}
    from .lesson_retrieval import lessons_payload
    lessons_skipped = None
    if role != 'survey' and payload.get('mode') != 'retrospective' and 'lessons' not in payload:
        lessons, lessons_skipped = lessons_payload(project, role, payload)
        if lessons is not None:
            payload = {**payload, 'lessons': lessons}
    from .role_profiles import enabled
    schema = schema_for(role, {**payload, **({"role_profile": True} if enabled(config) else {})})
    request = {
        "protocol": "storyloop.project-agent.v1",
        "role": role,
        "project": str(project),
        "payload": payload,
        "response_schema": wire_schema(schema),
    }
    instructions, skill_snapshots = _instruction_bundle(project, role, (config.get('profile') or {}).get('stacks', []))
    if role == 'review' and payload.get('mode') == 'retrospective':
        from .role_instructions import RETROSPECTIVE_NOTE
        instructions = RETROSPECTIVE_NOTE
    from .call_context import capability_for
    capability = capability_for(project, adapter_name)
    profile = build_profile(project, config, role, payload,
                            {'skill_snapshots': skill_snapshots, 'capability': capability})
    if profile is not None:
        profile["tier"] = settings["tier"]
        request["role_profile"] = profile
    _bounded_json(request)
    ledger = FOUNDATION_LEDGER if role == "survey" else _change_id(payload)
    call = open_call(project, role, ledger, adapter_name, request)
    if lessons_skipped:
        from .call_log import record_lessons_skipped
        record_lessons_skipped(project, call, lessons_skipped)
    from .call_context import context_manifest
    from .call_log import record_context
    record_context(project, call, context_manifest(project, payload, skill_snapshots))
    if profile is not None and capability.get('subagents') is not True:
        instructions += sequential_instructions(request)
    trace: dict = {"stderr": "", "sessions": [], "raw": None, "repairs": 0, "schema_errors": [],
                   "instructions": instructions, "skills": skill_snapshots, "telemetry": None}
    return _execute_call(project, role, payload, schema, request, adapter_config, settings,
                         degraded, profile, ledger, call, trace)


def _execute_call(project, role, payload, schema, request, adapter_config, settings,
                  degraded, profile, ledger, call, trace):
    adapter, adapter_name = adapter_config.get("kind"), settings["adapter"]
    started = time.monotonic()
    result = None
    error: Exception | None = None
    try:
        if adapter == "codex":
            model = model_for(request, adapter_config.get("models"))
            trace["codex_model"] = model
            trace["codex_profile"] = sequential_profile(request, "openai", model)
            trace["codex_instructions"] = sequential_instructions(request)
        result = invoke_transport({**request, "project": str(project)}, adapter_config, schema, trace)
        return result
    except Exception as exc:
        error = exc
        if isinstance(exc, StoryLoopError) and exc.code == "AGENT_UNAVAILABLE":
            exc.details.setdefault("role", role)
        raise
    finally:
        wall_ms = int((time.monotonic() - started) * 1000)
        telemetry = result.pop("_telemetry", None) if isinstance(result, dict) else trace["telemetry"]
        telemetry = dict(telemetry) if isinstance(telemetry, dict) else {}
        telemetry.pop("skills", None)
        telemetry["skills"] = trace["skills"]
        sessions = trace["sessions"] + _telemetry_sessions(telemetry)
        waiting = isinstance(error, StoryLoopError) and adapter == "manual" and error.code == "NEEDS_INPUT"
        # A manual adapter waiting for input is not a failed call; everything else that raised is.
        failed = result is None and not waiting
        evidence = _diagnostic(_record_memory_evidence, project, call, payload, result) or {}
        try:
            instructions_sha256 = hashlib.sha256(trace["instructions"].encode("utf-8")).hexdigest()
            schema_sha256 = hashlib.sha256(json.dumps(
                request.get("response_schema", schema), sort_keys=True,
                ensure_ascii=False).encode("utf-8")).hexdigest()
            record_call(project, role, ledger, telemetry, wall_ms, failed=failed,
                        call=call, session=",".join(dict.fromkeys(sessions)),
                        engine_fields={**evidence, "instructions_sha256": instructions_sha256,
                                       "schema_sha256": schema_sha256,
                                       **({"tier_requested": settings["tier"], "adapter": adapter_name,
                                        **({"degraded": degraded} if degraded else {})}
                                       if profile is not None else {})})
        except Exception:
            if error is None:
                raise
        finally:
            _diagnostic(close_call, project, call, status="WAITING" if waiting else "FAILED" if failed else "OK",
                       wall_ms=wall_ms, response=result, raw=trace["raw"], stderr=trace["stderr"],
                       error=str(error) if error else None, code=getattr(error, "code", None),
                       sessions=list(dict.fromkeys(sessions)), schema_errors=trace["schema_errors"],
                       repairs=trace["repairs"], semantic_repair="validation_errors" in payload)
            if not failed and not waiting and isinstance(result, dict):
                from .errata import capture_errata
                _diagnostic(capture_errata, project, result, role, call, _change_id(payload))


def _diagnostic(callback, *args, **kwargs):
    """Attempt each diagnostic independently without replacing the adapter outcome."""
    try:
        return callback(*args, **kwargs)
    except Exception:
        # Memory evidence and call closure are best-effort diagnostic writes.
        # Do not expose exception text or let one failure prevent the others.
        return None


def _record_memory_evidence(project, call, payload, result):
    from .lesson_retrieval import provided_ids, claimed_lessons
    from .call_log import record_lesson_evidence
    provided = None if payload.get('mode') == 'retrospective' else payload.get('lessons')
    claimed, unknown = claimed_lessons(provided, result or {})
    evidence = dict(zip(('lessons_provided', 'lessons_claimed', 'lessons_unknown'),
                        (provided_ids(provided), claimed, unknown)))
    evidence['lessons_truncated'] = any(len(value) > 20 for value in evidence.values())
    record_lesson_evidence(project, call, evidence)
    return evidence


def _telemetry_sessions(telemetry: object) -> list[str]:
    if not isinstance(telemetry, dict):
        return []
    value = telemetry.get("sessions")
    return [item for item in value if isinstance(item, str) and item] if isinstance(value, list) else []


def _change_id(payload: dict) -> str | None:
    value = payload.get("change_id")
    if not isinstance(value, str):
        change = payload.get("change")
        value = change.get("change_id") if isinstance(change, dict) else None
    return value if isinstance(value, str) and value else None
