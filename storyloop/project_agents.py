"""Fresh-process agents for product-level repository iterations."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time

from .baseline import load_project_config
from .models import StoryLoopError, require
from .foundation_survey import foundation_payload
from .skills import ROLE_SKILLS, skill_text
from .call_log import close_call, open_call
from .processutil import redact, run_process
from .role_schemas import normalize, schema_errors, schema_for, wire_schema
from .role_instructions import FOUNDATION_NOTE, INSTRUCTIONS
from .storage import load_json, write_json
from .telemetry import record_call

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
    text_ = INSTRUCTIONS[role] + REPAIR_NOTE
    for name in ROLE_SKILLS.get(role, ()):
        text_ += f"\n\nSkill {name}:\n" + skill_text(project, name)
    return text_


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
    foundation = foundation_payload(project) if role != "survey" else None
    if foundation and "foundation" not in payload:
        payload = {**payload, "foundation": foundation}
    schema = schema_for(role, payload)
    request = {
        "protocol": "storyloop.project-agent.v1",
        "role": role,
        "project": str(project),
        "payload": payload,
        "response_schema": wire_schema(schema),
    }
    _bounded_json(request)
    adapter = config.get("adapter")
    ledger = FOUNDATION_LEDGER if role == "survey" else _change_id(payload)
    call = open_call(project, role, ledger, adapter, request)
    trace: dict = {"stderr": "", "sessions": [], "raw": None, "repairs": 0, "schema_errors": []}
    started = time.monotonic()
    result = None
    error: StoryLoopError | None = None
    try:
        if adapter == "manual":
            result = _manual(project, request, schema, trace)
        elif adapter == "command":
            result = _command(project, request, config, schema, trace)
        else:
            require(adapter == "codex", "Unknown project agent adapter")
            result = _codex(project, request, config, schema, trace)
        return result
    except StoryLoopError as exc:
        error = exc
        if exc.code == "AGENT_UNAVAILABLE":
            exc.details.setdefault("role", role)
        raise
    finally:
        wall_ms = int((time.monotonic() - started) * 1000)
        telemetry = result.pop("_telemetry", None) if isinstance(result, dict) else None
        sessions = trace["sessions"] + _telemetry_sessions(telemetry)
        waiting = error is not None and adapter == "manual" and error.code == "NEEDS_INPUT"
        # A manual adapter waiting for input is not a failed call; everything else that raised is.
        failed = result is None and not waiting
        record_call(project, role, ledger, telemetry, wall_ms, failed=failed,
                    call=call, session=",".join(dict.fromkeys(sessions)))
        close_call(project, call, status="WAITING" if waiting else "FAILED" if failed else "OK",
                   wall_ms=wall_ms, response=result, raw=trace["raw"], stderr=trace["stderr"],
                   error=str(error) if error else None, code=error.code if error else None,
                   sessions=list(dict.fromkeys(sessions)), schema_errors=trace["schema_errors"],
                   repairs=trace["repairs"], semantic_repair="validation_errors" in payload)


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


def conform(value: dict, schema: dict) -> tuple[dict, list[str]]:
    """Normalize a role answer (nulls of optional fields dropped) and list its schema mismatches."""
    telemetry = value.get("_telemetry")
    body = normalize({key: item for key, item in value.items() if key != "_telemetry"}, schema)
    errors = schema_errors(body, schema)
    if telemetry is not None:
        body["_telemetry"] = telemetry
    return body, errors


def _require_conforming(value: dict, schema: dict, trace: dict) -> dict:
    body, errors = conform(value, schema)
    if errors:
        trace["schema_errors"] = errors
        raise StoryLoopError("Agent answer does not match the role's response schema: " + "; ".join(errors),
                             code="AGENT_CONTRACT_INVALID", details={"schema_errors": errors})
    return body


def _manual(project: Path, request: dict, schema: dict, trace: dict) -> dict:
    request_id = _request_id(request)
    envelope = {**request, "request_id": request_id, "instructions": instructions_for(project, request["role"])}
    request_path = project / ".storyloop" / "requests" / f"project-{request['role']}.json"
    response_path = project / ".storyloop" / "responses" / f"project-{request['role']}.json"
    write_json(request_path, envelope, max_bytes=2_000_000)
    if not response_path.exists():
        raise StoryLoopError(
            f"Manual input required. Read {request_path}; write {response_path} with the matching request_id.",
            code="NEEDS_INPUT",
        )
    response = load_json(response_path)
    require(response.get("request_id") == request_id, "Manual response does not match this request")
    result = response.get("result")
    require(isinstance(result, dict), "Manual response result must be an object")
    result = _require_conforming(result, schema, trace)
    response_path.unlink()
    return result


def _command(project: Path, request: dict, config: dict, schema: dict, trace: dict) -> dict:
    command = config.get("agent_command")
    require(isinstance(command, list) and bool(command), "Command adapter needs explicit argv")
    argv = [_expand(item, project) for item in command]
    require(all(isinstance(item, str) and item for item in argv), "Agent argv is invalid")
    executable = argv[0] if Path(argv[0]).is_absolute() else shutil.which(argv[0])
    if not executable:
        raise StoryLoopError("Agent executable is unavailable", code="AGENT_UNAVAILABLE")
    require(Path(executable).suffix.lower() not in {".cmd", ".bat", ".ps1"},
            "Use a native executable or explicit interpreter for the agent")
    argv[0] = str(executable)
    result = run_process(
        argv, project, config["agent_timeout_seconds"],
        input_text=json.dumps({**request, "instructions": instructions_for(project, request["role"])},
                              ensure_ascii=False),
    )
    trace["stderr"] = str(result.get("stderr") or "")
    _check_process(result)
    trace["raw"] = result.get("stdout", "")
    parsed = _parse(trace["raw"])
    trace["raw"] = None
    return _require_conforming(parsed, schema, trace)


CODEX_REPAIR_LIMIT = 2                 # resumes allowed to turn a bad final message into a valid answer
CODEX_MAX_OUTPUT = 16 * 1024 * 1024    # --json streams every event; far above the default cap
CODEX_REPAIR_PROMPT = (
    "上一条回复不是合法的 JSON 对象。不要再修改任何文件，也不要重复说明；"
    "只按原始要求输出一个 JSON 对象。"
)
CODEX_SCHEMA_REPAIR_PROMPT = (
    "上一条回复的 JSON 不符合要求的结构，问题如下：\n{errors}\n"
    "不要再修改任何文件，也不要重复说明；只输出修正后的完整 JSON 对象。"
)


def _codex(project: Path, request: dict, config: dict, schema: dict, trace: dict) -> dict:
    """Run Codex in a persistent session; resume it to repair a malformed final message.

    The role schema is enforced natively (`--output-schema`), which removes most format
    failures; what still goes wrong (prose, a mismatch the CLI let through) is repaired by
    resuming the same thread with the concrete problems. Sessions are kept on disk (no
    --ephemeral) so `codex exec resume` keeps the context. Usage comes from the --json events.
    """
    sandbox = "workspace-write" if request["role"] in {"implement", "characterize"} else "read-only"
    common = ["--ignore-user-config", "--color", "never", "--json"]
    prompt = instructions_for(project, request["role"]) + "\nRequest:\n" + json.dumps(request, ensure_ascii=False)
    telemetry = {"turns": 0, "input_tokens": 0, "output_tokens": 0, "segments": 0}
    thread: str | None = None
    next_prompt = prompt
    with tempfile.TemporaryDirectory(dir=project / ".storyloop", prefix="schema-") as folder:
        schema_file = Path(folder) / "response-schema.json"
        schema_file.write_text(json.dumps(request["response_schema"], ensure_ascii=False), encoding="utf-8")
        output_schema = ["--output-schema", str(schema_file)]
        for repair in range(CODEX_REPAIR_LIMIT + 1):
            if repair == 0:
                argv = _codex_executable() + ["exec", "--sandbox", sandbox, "-C", str(project),
                                              *common, *output_schema]
            else:
                argv = _codex_executable() + ["exec", "resume", thread, *common, *output_schema]
            trace["repairs"] = repair
            content = _codex_once(project, argv, next_prompt, config["agent_timeout_seconds"], telemetry, trace)
            thread = telemetry.pop("thread", None) or thread
            if thread and thread not in trace["sessions"]:
                trace["sessions"].append(thread)
            trace["raw"] = content
            try:
                result = _parse(content)
            except StoryLoopError:
                if not thread or repair == CODEX_REPAIR_LIMIT:
                    raise
                next_prompt = CODEX_REPAIR_PROMPT
                continue
            body, errors = conform(result, schema)
            if errors:
                trace["schema_errors"] = errors
                if thread and repair < CODEX_REPAIR_LIMIT:
                    next_prompt = CODEX_SCHEMA_REPAIR_PROMPT.format(errors="\n".join(errors))
                    continue
                return _require_conforming(result, schema, trace)
            trace["raw"], trace["schema_errors"] = None, []
            body["_telemetry"] = dict(telemetry)
            return body
    raise StoryLoopError("Project agent did not return a JSON object")  # unreachable


def _codex_executable() -> list[str]:
    """The Codex CLI argv; on Windows an npm .cmd/.ps1 shim is resolved to node + codex.js."""
    executable = shutil.which("codex.exe") or shutil.which("codex")
    if executable and Path(executable).suffix.lower() not in {".cmd", ".bat", ".ps1"}:
        return [executable]
    shim = executable or shutil.which("codex.cmd") or shutil.which("codex.ps1")
    if shim:
        root = Path(shim).parent
        entry = root / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
        node = str(root / "node.exe") if (root / "node.exe").is_file() else shutil.which("node")
        if entry.is_file() and node:
            return [node, str(entry)]
    raise StoryLoopError("Codex CLI is not available. Configure a manual or command adapter.",
                         code="AGENT_UNAVAILABLE")


def _codex_once(project: Path, argv: list[str], prompt: str, timeout: int, telemetry: dict,
                trace: dict) -> str:
    with tempfile.NamedTemporaryFile(dir=project / ".storyloop", suffix=".response",
                                     delete=False) as handle:
        output = Path(handle.name)
    try:
        result = run_process(argv + ["--output-last-message", str(output), "-"], project, timeout,
                             input_text=prompt, max_output=CODEX_MAX_OUTPUT)
        trace["stderr"] += str(result.get("stderr") or "")
        _codex_events(result.get("stdout", ""), telemetry)
        if telemetry.get("thread") and telemetry["thread"] not in trace["sessions"]:
            trace["sessions"].append(telemetry["thread"])
        _check_process(result)
        require(output.stat().st_size <= 2_000_000, "Agent response exceeded the size limit")
        return output.read_text(encoding="utf-8")
    finally:
        output.unlink(missing_ok=True)


def _codex_events(stream: str, telemetry: dict) -> None:
    """Fold the --json event stream into telemetry (thread id, turns, tokens)."""
    telemetry["segments"] += 1
    for line in stream.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") == "thread.started" and isinstance(event.get("thread_id"), str):
            telemetry["thread"] = event["thread_id"]
        elif event.get("type") == "turn.completed":
            usage = event.get("usage") or {}
            telemetry["turns"] += 1
            telemetry["input_tokens"] += int(usage.get("input_tokens") or 0)
            telemetry["output_tokens"] += int(usage.get("output_tokens") or 0)


def _check_process(result: dict) -> None:
    if (result.get("contained") is not True or result.get("timed_out")
            or result.get("truncated") or result.get("error")
            or result.get("returncode") != 0):
        reasons = [name for name in ("timed_out", "truncated", "error") if result.get(name)]
        if result.get("contained") is not True:
            reasons.append("not contained")
        if result.get("returncode") not in (0, None):
            reasons.append(f"exit {result['returncode']}")
        tail = redact(str(result.get("stderr") or "")).strip().splitlines()[-3:]
        detail = "; ".join(reasons) + (" | " + " / ".join(tail)[:600] if tail else "")
        # The agent never produced an answer (crash, timeout, rate limit, lost containment): an
        # infrastructure failure, distinct from an answer the loop can judge.
        raise StoryLoopError(f"Project agent process failed safely ({detail})", code="AGENT_UNAVAILABLE")


def _parse(output: str) -> dict:
    try:
        value = json.loads(output)
    except (TypeError, json.JSONDecodeError):
        raise StoryLoopError("Project agent must return exactly one JSON object") from None
    require(isinstance(value, dict), "Project agent response must be an object")
    return value


def _request_id(request: dict) -> str:
    content = json.dumps(request, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _bounded_json(value: dict) -> None:
    try:
        content = json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise StoryLoopError("Agent payload must be finite JSON") from exc
    require(len(content.encode("utf-8")) <= 2_000_000, "Agent payload exceeded the size limit")


def _expand(value: str, project: Path) -> str:
    return {"{python}": sys.executable, "{project}": str(project)}.get(value, value)
