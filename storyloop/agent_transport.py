"""Stateless manual, command and Codex transports with schema validation."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile

from .models import StoryLoopError, require
from .processutil import redact, run_process
from .role_schemas import normalize, schema_errors
from .storage import load_json, write_json

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


def invoke(request: dict, adapter_config: dict, schema: dict, trace: dict) -> dict:
    """Execute an already assembled request through its selected adapter."""
    project = Path(request["project"])
    kind = adapter_config.get("kind")
    if kind == "manual":
        return _manual(project, request, schema, trace)
    if kind == "command":
        return _command(project, request, adapter_config, schema, trace)
    require(kind == "codex", "Unknown project agent adapter")
    return _codex(project, request, adapter_config, schema, trace)


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
    envelope = {**request, "request_id": request_id, "instructions": trace["instructions"]}
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
    _capture_telemetry(result, trace)
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
        input_text=json.dumps({**request, "instructions": trace["instructions"]},
                              ensure_ascii=False),
    )
    trace["stderr"] = str(result.get("stderr") or "")
    trace["raw"] = result.get("stdout", "")
    try:
        _capture_telemetry(_parse(trace["raw"]), trace)
    except StoryLoopError:
        pass
    _check_process(result)
    parsed = _parse(trace["raw"])
    _capture_telemetry(parsed, trace)
    trace["raw"] = None
    return _require_conforming(parsed, schema, trace)


def _codex(project: Path, request: dict, config: dict, schema: dict, trace: dict) -> dict:
    """Run Codex with bounded schema repairs in the same persistent session."""
    sandbox = "workspace-write" if request["role"] in {"implement", "characterize"} else "read-only"
    model = trace["codex_model"]
    common = ["--ignore-user-config", "--color", "never", "--json"] + (["-m", model] if model else [])
    prompt = trace["instructions"] + trace["codex_instructions"] + "\nRequest:\n" + json.dumps(request, ensure_ascii=False)
    telemetry = {"turns": 0, "input_tokens": 0, "output_tokens": 0, "segments": 0}
    telemetry.update(trace["codex_profile"])
    trace["telemetry"] = telemetry
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


def _capture_telemetry(value: object, trace: dict) -> None:
    if isinstance(value, dict) and isinstance(value.get("_telemetry"), dict):
        trace["telemetry"] = value["_telemetry"]


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
