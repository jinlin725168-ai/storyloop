"""Fresh-process agents for product-level repository iterations."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time

from .agents import _codex_executable
from .baseline import load_project_config
from .models import StoryLoopError, require
from .processutil import run_process
from .storage import load_json, write_json
from .telemetry import record_call

ROLES = {"product", "smoke", "implement", "review"}
INSTRUCTIONS = {
    "product": (
        "Co-create only product-level intent. Return the complete StoryLoop product v1 JSON: "
        "goal, non_goals, unresolved business questions, user stories, and observable Given/When/Then "
        "acceptance criteria. Preserve stable IDs and never invent answers to unresolved questions."
    ),
    "smoke": (
        "Act as independent external acceptance QA. Return exactly {files:[{path,content}],manifest}. "
        "Generate a small core-flow smoke suite under the supplied relative suite root. Every automated "
        "criterion must be linked by ID. Tests must fail when behavior is absent and must not edit CI files."
    ),
    "implement": (
        "Implement the approved change in this repository. Modify only new files under configured module "
        "roots. If an integration or foundation file must change, explain that need without editing it. "
        "Never edit the foundation, OpenSpec contract, acceptance "
        "tests, reports, CI, or .storyloop. Do not commit or push. Run useful local checks. Return exactly "
        "{summary:string,questions:[string]}."
    ),
    "review": (
        "Independently review the real implementation against the approved stories and acceptance evidence. "
        "Do not edit files. Return exactly {verdict:'pass'|'fail'|'needs_input',summary:string," 
        "findings:[object],questions:[string]}. Pass only with no findings or questions."
    ),
}


def invoke_project_agent(project: Path, role: str, payload: dict) -> dict:
    """Invoke one stateless agent using the repository-local adapter configuration."""
    project = Path(project).resolve()
    require(role in ROLES, "Unknown project agent role")
    require(isinstance(payload, dict), "Agent payload must be an object")
    config = load_project_config(project)
    request = {
        "protocol": "storyloop.project-agent.v1",
        "role": role,
        "project": str(project),
        "payload": payload,
    }
    _bounded_json(request)
    adapter = config.get("adapter")
    started = time.monotonic()
    if adapter == "manual":
        result = _manual(project, request)
    elif adapter == "command":
        result = _command(project, request, config)
    else:
        require(adapter == "codex", "Unknown project agent adapter")
        result = _codex(project, request, config)
    telemetry = result.pop("_telemetry", None) if isinstance(result, dict) else None
    record_call(project, role, _change_id(payload), telemetry,
                int((time.monotonic() - started) * 1000))
    return result


def _change_id(payload: dict) -> str | None:
    value = payload.get("change_id")
    if not isinstance(value, str):
        change = payload.get("change")
        value = change.get("change_id") if isinstance(change, dict) else None
    return value if isinstance(value, str) and value else None


def _manual(project: Path, request: dict) -> dict:
    request_id = _request_id(request)
    envelope = {**request, "request_id": request_id, "instructions": INSTRUCTIONS[request["role"]]}
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
    response_path.unlink()
    return result


def _command(project: Path, request: dict, config: dict) -> dict:
    command = config.get("agent_command")
    require(isinstance(command, list) and bool(command), "Command adapter needs explicit argv")
    argv = [_expand(item, project) for item in command]
    require(all(isinstance(item, str) and item for item in argv), "Agent argv is invalid")
    executable = argv[0] if Path(argv[0]).is_absolute() else shutil.which(argv[0])
    require(bool(executable), "Agent executable is unavailable")
    require(Path(executable).suffix.lower() not in {".cmd", ".bat", ".ps1"},
            "Use a native executable or explicit interpreter for the agent")
    argv[0] = str(executable)
    result = run_process(
        argv, project, config["agent_timeout_seconds"],
        input_text=json.dumps({**request, "instructions": INSTRUCTIONS[request["role"]]}, ensure_ascii=False),
    )
    _check_process(result)
    return _parse(result.get("stdout", ""))


def _codex(project: Path, request: dict, config: dict) -> dict:
    root = project / ".storyloop"
    with tempfile.NamedTemporaryFile(dir=root, suffix=".response", delete=False) as handle:
        output = Path(handle.name)
    sandbox = "workspace-write" if request["role"] == "implement" else "read-only"
    command = _codex_executable() + [
        "exec", "--sandbox", sandbox, "--ephemeral", "--ignore-user-config",
        "--color", "never", "-C", str(project), "--output-last-message", str(output), "-",
    ]
    prompt = INSTRUCTIONS[request["role"]] + "\nRequest:\n" + json.dumps(request, ensure_ascii=False)
    try:
        result = run_process(command, project, config["agent_timeout_seconds"], input_text=prompt)
        _check_process(result)
        require(output.stat().st_size <= 2_000_000, "Agent response exceeded the size limit")
        return _parse(output.read_text(encoding="utf-8"))
    finally:
        output.unlink(missing_ok=True)


def _check_process(result: dict) -> None:
    if (result.get("contained") is not True or result.get("timed_out")
            or result.get("truncated") or result.get("error")
            or result.get("returncode") != 0):
        raise StoryLoopError("Project agent process failed safely")


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
