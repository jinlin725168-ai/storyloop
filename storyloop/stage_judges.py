"""Fail-closed execution and evidence contracts for one configured v2 Judge."""

from __future__ import annotations

import hashlib
import shutil
from collections.abc import Callable, Mapping
from pathlib import Path

from .boundaries import WorkspaceSnapshot, capture_snapshot, verify_unchanged
from .deadline import Clock, process_deadline
from .json_contract import dumps_strict_json, loads_strict_json
from .models import StoryLoopError, fields, identifier, require, strings, text
from .processutil import redact, run_process
from .stage_config import validate_loop_config

REQUEST_PROTOCOL = "loop.judge.request.v2"
RESPONSE_PROTOCOL = "loop.judge.v2"
RESPONSE_FIELDS = {
    "protocol", "request_id", "stage", "judge", "phase", "status", "summary", "findings",
}
EXIT_CODES = {"pass": 0, "fail": 1, "error": 2, "skip": 3}
SCRIPT_SUFFIXES = {".cmd", ".bat", ".ps1"}


def run_stage_judge(project: Path, config: dict, stage_id: str, judge_id: str,
                    context: dict, resolver: Callable[[str], str | None] = shutil.which,
                    environ: Mapping[str, str] | None = None,
                    attempt_deadline_ms: int | None = None,
                    budget_clock_ms: Clock | None = None) -> dict:
    """Run one exact Judge, prove read-only behavior, and return bounded evidence."""
    require(isinstance(context, dict), "Judge context must be a JSON object")
    dumps_strict_json(context, "Judge context", sort_keys=True)
    stage, judge = _select_judge(config, stage_id, judge_id)
    command = _resolve_command(judge["command"], resolver)
    project = project.resolve()
    before = _capture_judge_snapshot(project)
    before_digest = _snapshot_digest(before)
    command_digest = _command_digest(command)
    request = _request(stage_id, judge, context, before_digest, command_digest)
    timeout, deadline = process_deadline(
        _timeout(config), attempt_deadline_ms, budget_clock_ms,
    )
    process = run_process(
        command, project, timeout, input_text=dumps_strict_json(request, "Judge request"),
        environment_allowlist=config["agent"]["environment_allowlist"],
        source_environment=environ, absolute_deadline=deadline,
    )
    if process.get("contained") is not True:
        raise StoryLoopError(
            "Judge process was not securely contained",
            code="UNSAFE_CONTAINMENT",
        )
    after = _capture_judge_snapshot(project)
    verify_unchanged(before, after)
    _check_process(process)
    response = _parse_response(process["stdout"], request)
    _check_exit_code(response["status"], process.get("returncode"))
    return _evidence(response, process["returncode"], before, after, command_digest)


def _capture_judge_snapshot(project: Path) -> WorkspaceSnapshot:
    try:
        return capture_snapshot(project, include_control=True)
    except StoryLoopError as exc:
        if exc.code == "WORKSPACE_TAINTED":
            raise
        raise StoryLoopError(str(exc), code="WORKSPACE_TAINTED") from exc

def _select_judge(config: dict, stage_id: str, judge_id: str) -> tuple[dict, dict]:
    validate_loop_config(config)
    identifier(stage_id, "stage id")
    identifier(judge_id, "judge id")
    stage = next((item for item in config["stages"] if item["id"] == stage_id), None)
    require(stage is not None, "Unknown stage")
    judge = next((item for item in stage["judges"] if item["id"] == judge_id), None)
    require(judge is not None, "Unknown judge")
    return stage, judge


def _resolve_command(command: list[str], resolver: Callable[[str], str | None]) -> list[str]:
    require(Path(command[0]).suffix.lower() not in SCRIPT_SUFFIXES,
            "Use a native executable or explicit interpreter for the Judge command")
    executable = resolver(command[0])
    require(isinstance(executable, str) and bool(executable)
            and Path(executable).suffix.lower() not in SCRIPT_SUFFIXES,
            "Judge executable is not available or is not native")
    return [executable, *command[1:]]


def _request(stage_id: str, judge: dict, context: dict, workspace_digest: str,
             command_digest: str) -> dict:
    base = {"protocol": REQUEST_PROTOCOL, "stage": stage_id, "judge": judge["id"],
            "phase": judge["phase"], "context": context,
            "workspace_digest": workspace_digest, "command_digest": command_digest}
    serialized = dumps_strict_json(base, "Judge request identity", sort_keys=True)
    return {**base, "request_id": hashlib.sha256(serialized.encode("utf-8")).hexdigest()}


def _command_digest(command: list[str]) -> str:
    serialized = dumps_strict_json(command, "Judge command identity", sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _timeout(config: dict) -> int:
    return min(config["agent"]["timeout_seconds"], config["budgets"]["total_seconds"])


def _check_process(process: dict) -> None:
    if process.get("timed_out"):
        raise StoryLoopError("Judge exceeded its time budget")
    if process.get("truncated"):
        raise StoryLoopError("Judge output exceeded the output limit")
    if process.get("error"):
        raise StoryLoopError("Judge process failed; check its executable and environment")


def _parse_response(output: str, request: dict) -> dict:
    value = loads_strict_json(output, "Judge response")
    fields(value, RESPONSE_FIELDS)
    require(value["protocol"] == RESPONSE_PROTOCOL, "Unknown Judge response protocol")
    for key in ("request_id", "stage", "judge", "phase"):
        require(value[key] == request[key], "Judge response does not match current request")
    require(value["status"] in EXIT_CODES, "Unknown Judge status")
    text(value["summary"], "Judge summary")
    findings = strings(value["findings"], "Judge finding")
    require(len(findings) <= 100, "Too many Judge findings")
    require(value["status"] != "fail" or bool(findings), "Judge fail requires findings")
    require(value["status"] != "pass" or not findings, "Judge pass cannot contain findings")
    value["summary"] = redact(value["summary"])[:4000]
    value["findings"] = [redact(item)[:4000] for item in findings]
    return value


def _check_exit_code(status: str, returncode: object) -> None:
    require(returncode == EXIT_CODES[status], "Judge status and exit code are inconsistent")


def _evidence(response: dict, returncode: int, before: WorkspaceSnapshot,
              after: WorkspaceSnapshot, command_digest: str) -> dict:
    outcome = _outcome(response["phase"], response["status"])
    return {**response, "outcome": outcome, "exit_code": returncode, "contained": True,
            "command_digest": command_digest, "before_digest": _snapshot_digest(before),
            "after_digest": _snapshot_digest(after)}


def _outcome(phase: str, status: str) -> str:
    if status == "error":
        return "ERROR"
    if status == "skip":
        return "SKIP"
    if phase == "red":
        return "RED" if status == "fail" else "EXISTING_GREEN"
    return "GREEN" if status == "pass" else "FAIL"


def _snapshot_digest(snapshot: WorkspaceSnapshot) -> str:
    digest = hashlib.sha256()
    for path, fingerprint in snapshot.entries:
        values = (path, fingerprint.kind, str(fingerprint.size), fingerprint.sha256,
                  str(fingerprint.mode), str(fingerprint.attributes),
                  str(fingerprint.mtime_ns), str(fingerprint.uid), str(fingerprint.gid))
        digest.update("\0".join(values).encode("utf-8") + b"\0")
    return digest.hexdigest()