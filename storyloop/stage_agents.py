"""Selected-only, fail-closed agent adapters for the stage-oriented v2 loop."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path

from .deadline import Clock, ensure_time_remaining, process_deadline
from .json_contract import dumps_strict_json, loads_strict_json
from .models import StoryLoopError, fields, identifier, require, strings, text
from .processutil import run_process
from .stage_config import select_adapter
from .storage import write_json

PROTOCOL = "loop.agent.v2"
ROLES = {"implement", "review", "replan"}
RESPONSE_FIELDS = {"protocol", "request_id", "status", "summary", "questions"}
REQUEST_FIELDS = {
    "protocol", "adapter", "role", "stage", "instruction", "payload", "request_id",
}
STATUSES = {"ok", "needs_input", "architecture_decision_required"}
SCRIPT_SUFFIXES = {".cmd", ".bat", ".ps1"}
MAX_REQUEST_BYTES = 1024 * 1024
MAX_RESPONSE_BYTES = 1024 * 1024
ROLE_INSTRUCTIONS = {
    "implement": "Implement only the selected stage within its write boundaries.",
    "review": "Inspect the selected stage independently; do not declare Judge success.",
    "replan": "Propose a bounded retry plan without changing protected architecture or specs.",
}


def adapter_control_root(project: Path) -> Path:
    """Return the persistent v2 control plane outside the Agent write root."""
    project = project.resolve()
    identity = hashlib.sha256(str(project).casefold().encode("utf-8")).hexdigest()[:12]
    return project.parent / f".{project.name}.storyloop-{identity}" / "v2"


def probe_selected_adapter(config: dict, requested: str | None = None,
                           environ: Mapping[str, str] | None = None,
                           resolver: Callable[[str], str | None] = shutil.which) -> dict:
    """Resolve only the adapter selected by the shared configuration precedence."""
    name, adapter = select_adapter(config, requested, environ)
    kind = adapter["kind"]
    if kind == "manual":
        command: list[str] = []
    elif kind == "codex":
        command = _resolve_codex(resolver)
    else:
        command = _resolve_command(adapter["command"], resolver)
    return {"name": name, "kind": kind, "command": command}


def invoke_selected_adapter(project: Path, config: dict, role: str, stage: str,
                            payload: dict, requested: str | None = None,
                            environ: Mapping[str, str] | None = None,
                            resolver: Callable[[str], str | None] = shutil.which,
                            attempt_deadline_ms: int | None = None,
                            budget_clock_ms: Clock | None = None) -> dict:
    """Invoke one v2 adapter and return a validated non-verdict response."""
    ensure_time_remaining(attempt_deadline_ms, budget_clock_ms)
    require(role in ROLES, "Unknown agent role")
    identifier(stage, "stage id")
    require(isinstance(payload, dict), "Agent payload must be a JSON object")
    selected = probe_selected_adapter(config, requested, environ, resolver)
    require(any(item["id"] == stage for item in config["stages"]), "Unknown stage")
    request = _request(selected["name"], role, stage, payload)
    project = project.resolve()
    if selected["kind"] == "manual":
        response = _manual(project, request)
    elif selected["kind"] == "codex":
        response = _codex(project, config, selected["command"], role, request, environ,
                          attempt_deadline_ms, budget_clock_ms)
    else:
        response = _command(project, config, selected["command"], request, environ,
                            attempt_deadline_ms, budget_clock_ms)
    return _status(response)


def _resolve_command(command: list[str], resolver: Callable[[str], str | None]) -> list[str]:
    require(_is_native(command[0]),
            "Use a native executable or explicit interpreter for the adapter command")
    executable = resolver(command[0])
    require(_is_native(executable),
            "Selected adapter executable is not available or is not native")
    return [executable, *command[1:]]


def _is_native(executable: object) -> bool:
    return (isinstance(executable, str) and bool(executable)
            and Path(executable).suffix.lower() not in SCRIPT_SUFFIXES)


def _resolve_codex(resolver: Callable[[str], str | None]) -> list[str]:
    executable = resolver("codex.exe") or resolver("codex")
    if _is_native(executable):
        return [executable]
    shim = executable or resolver("codex.cmd") or resolver("codex.ps1")
    if shim:
        root = Path(shim).parent
        entry = root / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
        local_node = str(root / "node.exe") if (root / "node.exe").is_file() else None
        node = local_node or resolver("node")
        if entry.is_file() and _is_native(node):
            return [node, str(entry)]
    raise StoryLoopError("Selected Codex adapter executable is not available")


def _request(adapter: str, role: str, stage: str, payload: dict) -> dict:
    base = {"protocol": PROTOCOL, "adapter": adapter, "role": role, "stage": stage,
            "instruction": ROLE_INSTRUCTIONS[role], "payload": payload}
    serialized = _serialize(base, sort_keys=True)
    return {**base, "request_id": hashlib.sha256(serialized.encode("utf-8")).hexdigest()}


def _serialize(value: object, sort_keys: bool = False) -> str:
    return dumps_strict_json(value, "Agent payload", MAX_REQUEST_BYTES, sort_keys)


def _command(project: Path, config: dict, command: list[str], request: dict,
             environ: Mapping[str, str] | None, attempt_deadline_ms: int | None,
             budget_clock_ms: Clock | None) -> dict:
    timeout, deadline = process_deadline(
        config["agent"]["timeout_seconds"], attempt_deadline_ms, budget_clock_ms,
    )
    result = run_process(
        command, project, timeout, input_text=_serialize(request),
        environment_allowlist=config["agent"]["environment_allowlist"],
        source_environment=environ, absolute_deadline=deadline,
    )
    _check_process(result)
    return _parse_response(result["stdout"], request["request_id"])


def _codex(project: Path, config: dict, command: list[str], role: str, request: dict,
           environ: Mapping[str, str] | None, attempt_deadline_ms: int | None,
           budget_clock_ms: Clock | None) -> dict:
    sandbox = "workspace-write" if role == "implement" else "read-only"
    try:
        with tempfile.TemporaryDirectory(prefix=f".{project.name}.storyloop-",
                                         dir=project.parent) as folder:
            output_path = Path(folder) / "response.json"
            output_path.touch(exist_ok=False)
            argv = command + _codex_arguments(project, sandbox, output_path)
            timeout, deadline = process_deadline(
                config["agent"]["timeout_seconds"], attempt_deadline_ms, budget_clock_ms,
            )
            result = run_process(
                argv, project, timeout,
                input_text=_serialize(request),
                environment_allowlist=config["agent"]["environment_allowlist"],
                source_environment=environ, absolute_deadline=deadline,
            )
            _check_process(result)
            output = _read_bounded_regular_file(output_path)
            return _parse_response(output, request["request_id"])
    except StoryLoopError:
        raise
    except (OSError, UnicodeError):
        raise StoryLoopError("Cannot access the selected adapter response") from None


def _codex_arguments(project: Path, sandbox: str, output_path: Path) -> list[str]:
    return ["exec", "--sandbox", sandbox, "--ephemeral", "--ignore-user-config",
            "--skip-git-repo-check", "--color", "never", "-C", str(project),
            "--output-last-message", str(output_path), "-"]


def _check_process(result: dict) -> None:
    if result.get("contained") is not True:
        raise StoryLoopError(
            "External adapter process was not securely contained",
            code="UNSAFE_CONTAINMENT",
        )
    if result.get("timed_out"):
        raise StoryLoopError("Agent exceeded its time budget")
    if result.get("truncated"):
        raise StoryLoopError("Agent output exceeded the output limit")
    if result.get("error") or result.get("returncode") != 0:
        raise StoryLoopError("Agent process failed; check adapter installation and configuration")


def _parse_response(output: str, request_id: str) -> dict:
    value = loads_strict_json(output, "Agent response", MAX_RESPONSE_BYTES)
    fields(value, RESPONSE_FIELDS)
    require(value["protocol"] == PROTOCOL, "Unknown agent response protocol")
    require(value["request_id"] == request_id, "Agent response does not match current request")
    require(value["status"] in STATUSES, "Unknown agent response status")
    text(value["summary"], "agent summary")
    strings(value["questions"], "agent questions")
    require(value["status"] == "ok" or bool(value["questions"]),
            "Nonterminal response requires questions")
    require(value["status"] != "ok" or not value["questions"],
            "ok response cannot contain unresolved questions")
    return value


def _read_bounded_regular_file(path: Path) -> str:
    try:
        before = path.lstat()
        _validate_file_identity(before)
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as handle:
            current = os.fstat(handle.fileno())
            _validate_file_identity(current)
            require((before.st_dev, before.st_ino) == (current.st_dev, current.st_ino),
                    "Adapter response file changed unexpectedly")
            content = handle.read(MAX_RESPONSE_BYTES + 1)
        require(len(content) <= MAX_RESPONSE_BYTES,
                "Adapter response must be a bounded regular file")
        return content.decode("utf-8-sig")
    except StoryLoopError:
        raise
    except (OSError, UnicodeError):
        raise StoryLoopError("Adapter response must be a bounded regular file") from None


def _validate_file_identity(status: os.stat_result) -> None:
    require(stat.S_ISREG(status.st_mode) and status.st_nlink == 1
            and status.st_size <= MAX_RESPONSE_BYTES,
            "Adapter response must be a bounded regular file")


def expected_manual_request_id(config: dict, role: str, stage: str,
                               payload: dict, requested: str | None = None,
                               environ: Mapping[str, str] | None = None) -> str | None:
    """Return the exact current manual request identity, or None for non-manual."""
    require(role in ROLES, "Unknown agent role")
    identifier(stage, "stage id")
    require(isinstance(payload, dict), "Agent payload must be a JSON object")
    name, adapter = select_adapter(config, requested, environ)
    require(any(item["id"] == stage for item in config["stages"]), "Unknown stage")
    if adapter["kind"] != "manual":
        return None
    return _request(name, role, stage, payload)["request_id"]

def discover_manual_ack(project: Path, stage: str, expected_request_id: str,
                        role: str = "implement") -> dict | None:
    """Recover a validated pending manual receipt without exposing its payload."""
    identifier(stage, "stage id")
    require(role in ROLES, "Unknown agent role")
    require(isinstance(expected_request_id, str) and
            re.fullmatch(r"[0-9a-f]{64}", expected_request_id),
            "Invalid expected manual request")
    name = f"{stage}.{role}.json"
    root = adapter_control_root(project)
    request_path = root / "requests" / name
    response_path = root / "responses" / name
    if not response_path.exists():
        return None
    try:
        request = loads_strict_json(
            _read_bounded_regular_file(request_path), "Manual request", MAX_REQUEST_BYTES,
        )
        fields(request, REQUEST_FIELDS)
        require(request["protocol"] == PROTOCOL and request["stage"] == stage
                and request["role"] == role, "Manual request identity does not match")
        if request["request_id"] != expected_request_id:
            return None
        wrapper = loads_strict_json(
            _read_bounded_regular_file(response_path),
            "Manual response", MAX_RESPONSE_BYTES,
        )
        fields(wrapper, {"request_id", "result"})
        require(wrapper["request_id"] == request["request_id"],
                "Manual response does not match current request")
        result = _parse_response(_serialize(wrapper["result"]), request["request_id"])
        require(result["status"] == "ok", "Manual response is not complete")
        return _manual_ack(request)
    except StoryLoopError:
        raise StoryLoopError("Pending manual response is invalid") from None


def _manual_ack(request: dict) -> dict:
    return {
        "protocol": "loop.manual.ack.v2",
        "stage": request["stage"],
        "role": request["role"],
        "request_id": request["request_id"],
    }

def consume_manual_ack(project: Path, acknowledgement: dict) -> None:
    """Idempotently consume the exact manual receipt after durable success."""
    fields(acknowledgement, {"protocol", "stage", "role", "request_id"})
    require(acknowledgement["protocol"] == "loop.manual.ack.v2",
            "Unknown manual acknowledgement protocol")
    identifier(acknowledgement["stage"], "stage id")
    require(acknowledgement["role"] in ROLES, "Unknown manual acknowledgement role")
    request_id = acknowledgement["request_id"]
    require(isinstance(request_id, str) and re.fullmatch(r"[0-9a-f]{64}", request_id),
            "Invalid manual acknowledgement request")
    name = f"{acknowledgement['stage']}.{acknowledgement['role']}.json"
    response_path = adapter_control_root(project) / "responses" / name
    if not response_path.exists():
        return
    try:
        response = loads_strict_json(
            _read_bounded_regular_file(response_path),
            "Manual response", MAX_RESPONSE_BYTES,
        )
        fields(response, {"request_id", "result"})
        require(response["request_id"] == request_id,
                "Manual response does not match durable acknowledgement")
        response_path.unlink()
    except StoryLoopError:
        raise
    except OSError:
        if response_path.exists():
            raise StoryLoopError("Cannot consume the manual response") from None

def _manual(project: Path, request: dict) -> dict:
    name = f"{request['stage']}.{request['role']}.json"
    root = adapter_control_root(project)
    request_path, response_path = root / "requests" / name, root / "responses" / name
    write_json(request_path, request)
    if not response_path.exists():
        raise StoryLoopError(
            f"Manual input required for {name}", code="NEEDS_INPUT",
            details={"request_path": str(request_path), "response_path": str(response_path)},
        )
    try:
        response = loads_strict_json(_read_bounded_regular_file(response_path),
                                     "Manual response", MAX_RESPONSE_BYTES)
        fields(response, {"request_id", "result"})
        require(response["request_id"] == request["request_id"],
                "Manual response does not match current request")
        result = _parse_response(_serialize(response["result"]), request["request_id"])
    except StoryLoopError:
        raise StoryLoopError("Manual response must be valid, bounded, and match the request",
                             code="NEEDS_INPUT") from None
    if result["status"] == "ok":
        result = {
            **result,
            "_manual_ack": _manual_ack(request),

        }
    return result


def _status(response: dict) -> dict:
    codes = {"needs_input": "NEEDS_INPUT",
             "architecture_decision_required": "ARCHITECTURE_DECISION_REQUIRED"}
    if response["status"] in codes:
        raise StoryLoopError(response["summary"], code=codes[response["status"]],
                             details={"questions": list(response["questions"])})
    return response