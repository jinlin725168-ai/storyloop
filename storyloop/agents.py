"""Fresh-process agent adapters; the runner owns validation and completion."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile

from .models import StoryLoopError, validate_config
from .processutil import run_process
from .storage import load_json, write_json, approval_digest, workspace_digest

ROLES = {"product", "prepare", "implement", "review"}
PRODUCT_CONTRACT = {
    "version": 1, "goal": "text", "non_goals": ["text"],
    "questions": [{"id": "Q1", "text": "business decision", "options": ["option"]}],
    "stories": [{"id": "S1", "title": "text", "as_a": "actor", "want": "outcome",
                 "so_that": "benefit", "rules": ["rule"], "out_of_scope": ["text"],
                 "depends_on": [], "acceptance": [{"id": "A1", "given": "context",
                 "when": "action", "then": "observable outcome", "kind": "automated|manual"}]}],
}
CHECK_CONTRACT = {"version": 1, "checks": [{
    "id": "C1", "story_id": "S1", "kind": "health|acceptance|regression", "criteria": ["A1"],
    "command": ["{python}", "{qa}/check.py"], "expected_tests": ["behavior_name"],
    "timeout_seconds": 60,
}]}
ROLE_INSTRUCTIONS = {
    "product": "Co-create the product with the human. Preserve stable IDs and previous decisions. "
        "Ask only unresolved business questions, record scope boundaries and concrete examples. "
        "Do not silently resolve or delete unanswered questions. Return a complete product JSON.",
    "prepare": "Act as independent QA. Return executable, criterion-linked checks and initial "
        "scaffolding. Do not execute generated code and do not edit files. Never replace existing "
        "workspace files. Include environment health checks before behavioral checks. Each automated "
        "criterion needs an acceptance check. Manual criteria remain manual. Checks must test real "
        "behavior, fail for missing behavior, and distinguish errors/skips from behavioral failure.",
    "implement": "Implement the selected story using RED, minimal GREEN, then refactor. "
        "This invocation already grants workspace-write access and permission to run local, "
        "non-network tests. Use those capabilities directly; do not ask the human to rerun with "
        "permissions unless an attempted tool call actually fails. "
        "Change only workspace files. Treat product, qa, checks and .storyloop as read-only. "
        "Never weaken tests, fabricate evidence, or claim controller completion. Return "
        "{summary:string, questions:[string]}. Questions are only for genuine product ambiguities, "
        "never routine implementation choices or already-granted tool access.",
    "review": "Independently evaluate the running implementation against the original story and "
        "acceptance criteria. Inspect real code, run relevant existing checks and explore boundary "
        "cases read-only. Do not accept an implementation summary as evidence. Return "
        "{verdict:'pass'|'fail'|'needs_input',summary:string,findings:[{criterion_id:string,"
        "message:string}],questions:[string]}. Pass requires no unresolved findings or questions. "
        "Do not claim manual human acceptance. Use only criterion IDs from the product.",
}


def invoke(project: Path, role: str, payload: dict) -> dict:
    """Invoke one role with a fresh process, or consume an explicit manual response."""
    if role not in ROLES:
        raise StoryLoopError("Unknown agent role.")
    project = project.resolve()
    config = validate_config(load_json(project / "config.json"))
    request = {"protocol": "storyloop.agent.v1", "role": role,
               "project": str(project), "workspace": str(project / "workspace"),
               "qa": str(project / "qa"), "payload": payload}
    timeout = min(config["agent_timeout_seconds"], float(payload.get("timeout_seconds", config["agent_timeout_seconds"])))
    if timeout <= 0:
        raise StoryLoopError("Agent time budget exhausted.")
    request["approval_digest"] = approval_digest(project)
    if role in {"product", "prepare", "review"}:
        request["workspace_digest"] = workspace_digest(project)
    adapter = config["adapter"]
    if adapter == "manual":
        return _manual(project, request)
    if adapter == "codex":
        return _codex(project, request, timeout)
    command = [_expand(arg, project) for arg in config["agent_command"]]
    if not command or Path(command[0]).suffix.lower() in {".cmd", ".bat", ".ps1"}:
        raise StoryLoopError("Use a native executable or explicit interpreter for agent_command.")
    result = run_process(command, project / "workspace", timeout,
                         input_text=json.dumps(request, ensure_ascii=False))
    _check_process(result)
    return _parse_response(result.get("stdout", ""))


def _expand(value: str, project: Path) -> str:
    replacements = {"{python}": sys.executable, "{project}": str(project),
                    "{workspace}": str(project / "workspace")}
    return replacements.get(value, value)


def _check_process(result: dict) -> None:
    if result.get("timed_out"):
        raise StoryLoopError("Agent exceeded its time budget.")
    if result.get("truncated"):
        raise StoryLoopError("Agent output exceeded the output limit.")
    if result.get("error") or result.get("returncode") != 0:
        raise StoryLoopError("Agent process failed; check adapter installation and configuration.")


def _parse_response(output: str) -> dict:
    try:
        result = json.loads(output)
    except (json.JSONDecodeError, TypeError):
        raise StoryLoopError("Agent must return exactly one valid JSON object.") from None
    if not isinstance(result, dict):
        raise StoryLoopError("Agent response must be a JSON object.")
    return result


def _semantic_request(value: object) -> object:
    """Exclude retry bookkeeping; identical semantic requests can resume."""
    if isinstance(value, dict):
        return {key: _semantic_request(item) for key, item in value.items()
                if key not in {"attempt", "seconds", "timeout_seconds"}}
    if isinstance(value, list):
        return [_semantic_request(item) for item in value]
    return value


def _manual(project: Path, request: dict) -> dict:
    serialized = json.dumps(_semantic_request(request), sort_keys=True, ensure_ascii=False)
    request_id = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    request = {**request, "request_id": request_id, "instructions": _instructions(request["role"])}
    root = project / ".storyloop"
    request_path = root / "requests" / (request["role"] + ".json")
    response_path = root / "responses" / (request["role"] + ".json")
    write_json(request_path, request)
    if not response_path.exists():
        raise StoryLoopError(f"Manual input required. Read {request_path}; write "
            f"{response_path} as {{request_id: <matching ID>, result: <response object>}}.",
            code="NEEDS_INPUT")
    response = load_json(response_path)
    if not isinstance(response, dict) or response.get("request_id") != request_id:
        raise StoryLoopError("Manual response does not match the current request.", code="NEEDS_INPUT")
    result = response.get("result")
    if not isinstance(result, dict):
        raise StoryLoopError("Manual response result must be a JSON object.", code="NEEDS_INPUT")
    response_path.unlink()
    return result


def _instructions(role: str) -> str:
    instructions = ROLE_INSTRUCTIONS[role]
    if role == "product":
        instructions += " Product schema: " + json.dumps(PRODUCT_CONTRACT)
    if role == "prepare":
        instructions += " Return {checks:<checks object>, qa_files:[{path,content}], "
        instructions += "workspace_files:[{path,content}]}. Paths are relative to qa/ or workspace/. "
        instructions += "Checks schema: " + json.dumps(CHECK_CONTRACT)
        instructions += " Check stdout protocol: {protocol:'storyloop.check.v1',results:[" \
            "{id:string,status:'pass'|'fail'|'error'|'skip',message:string}]}. IDs must equal " \
            "expected_tests exactly. Exit 0 only if all pass; exit 1 only for behavioral " \
            "failure without error/skip. Errors/skips must use a different exit code."
    return instructions + " Output only final JSON. Never include credentials or sensitive data."


def _codex_executable() -> list[str]:
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
    raise StoryLoopError("Codex CLI is not available. Configure a manual or command adapter.")


def _codex(project: Path, request: dict, timeout: int) -> dict:
    root = project / ".storyloop"
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=root, suffix=".response", delete=False) as handle:
        output_path = Path(handle.name)
    sandbox = "workspace-write" if request["role"] == "implement" else "read-only"
    command = _codex_executable() + ["exec", "--sandbox", sandbox, "--ephemeral",
        "--ignore-user-config", "--skip-git-repo-check", "--color", "never",
        "-C", str(project / "workspace"), "--output-last-message", str(output_path), "-"]
    prompt = _instructions(request["role"]) + "\nRequest:\n" + json.dumps(request, ensure_ascii=False)
    try:
        result = run_process(command, project / "workspace", timeout, input_text=prompt)
        _check_process(result)
        if output_path.stat().st_size > 2_000_000:
            raise StoryLoopError("Agent final response exceeded the output limit.")
        return _parse_response(output_path.read_text(encoding="utf-8"))
    finally:
        output_path.unlink(missing_ok=True)
