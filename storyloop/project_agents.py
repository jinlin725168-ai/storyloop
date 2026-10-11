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
from .skills import skill_text
from .call_log import close_call, open_call
from .processutil import redact, run_process
from .role_schemas import normalize, schema_errors, schema_for, wire_schema
from .storage import load_json, write_json
from .telemetry import record_call

ROLES = {"product", "acceptance", "implement", "review", "survey", "characterize", "design"}
FOUNDATION_NOTE = (
    "payload.foundation (when present) lists the protected knowledge base under openspec/foundation/ "
    "(modules, architecture, extension mechanism, API surface, capabilities, building blocks, testing). "
    "Read the documents relevant to your role before anything else and treat them as the authority on "
    "what the base already provides and how it is extended. "
)
INSTRUCTIONS = {
    "product": (
        "Co-create only product-level intent and return the complete StoryLoop product v1 JSON. "
        + FOUNDATION_NOTE + "Never re-specify a capability the base already provides: reference it, "
        "and when a story changes an existing requirement reuse that requirement's title. "
        "Structure (spec-kit style): goal; non_goals; stories ordered by priority P1/P2/P3, each "
        "independently testable (`independent_test` says how to verify it alone), with `rules` "
        "written as EARS statements (\"WHEN <trigger> THE SYSTEM SHALL <behavior>\" or "
        "\"THE SYSTEM SHALL ...\", one testable obligation each) and Given/When/Then acceptance "
        "criteria that follow Gherkin discipline: one observable behavior per criterion, declarative "
        "and verifiable through the product's API wherever the behavior is not inherently visual; UI-only "
        "criteria (a page is reachable, the primary action completes, the primary validation shows) are the "
        "core flow only, at most three per story, because browser tests written before the UI exists are fragile; "
        "wording (what happens, never which button or SQL), Given for state, When for exactly one "
        "action, Then for the observable outcome. Cover the primary path, alternates, and failure "
        "cases that matter; kind is 'manual' only when no machine can observe the outcome. "
        "Size the contract to the request: a bounded change gets one or two stories, never a "
        "sprawling backlog. Unresolved product ambiguities become `questions`: scan functional "
        "scope, domain/data, interaction flow, edge cases and failure handling, integration, "
        "constraints, terminology and completion signals; ask at most 5 per revision, highest "
        "impact first, each with 2 to 5 mutually exclusive `options`, a `recommended` option and "
        "a one-sentence `why`. Never invent answers; mark the affected rule "
        "\"[NEEDS CLARIFICATION: Qn]\" instead. Preserve stable IDs and every earlier "
        "`clarifications` entry; when the request carries answers, apply them to rules and "
        "criteria, drop the answered questions, and keep the decisions in `clarifications`. "
        "Exact shape: {version:1, goal:string, non_goals:[string], capability?:slug, "
        "clarifications?:[{session,question,answer}], questions:[{id,text,options:[string],"
        "recommended?:string,why?:string}], stories:[{id,title,priority:'P1'|'P2'|'P3',"
        "independent_test:string,as_a,want,so_that,rules:[string],out_of_scope:[string],"
        "depends_on:[string],acceptance:[{id,given,when,then,kind:'automated'|'manual'}]}]}; "
        "every non-optional key is required even when empty, IDs unique across the product, no extra keys. "
        "Write all human-facing text (goal, stories, rules, criteria, questions) in the language of the request. "
        "If openspec/specs/<capability>/spec.md exists, it is the current behavior: a story that changes an "
        "existing requirement must reuse that requirement's title, restate every existing scenario with its "
        "original criterion id (updating steps where behavior changes), and add new ids only for new scenarios."
    ),
    "acceptance": (
        "Act as independent external acceptance QA. Return exactly {files:[{path,content}],manifest}. "
        + FOUNDATION_NOTE + "Use testing.md and building-blocks.md for the acceptance client, how the "
        "app is started, and the API conventions. "
        "Generate a small core-flow smoke suite under payload.suite_root: verify every criterion that can be "
        "observed through the API with API tests, and use a browser only for criteria that are inherently visual. "
        "If acceptance/ui/ exists it holds "
        "verified plans and Playwright specs of existing screens: reuse their locators, texts and flows. Every automated "
        "criterion must be linked by ID. Tests must fail when behavior is absent and must not edit CI files. "
        "Exact shape: {files:[{path,content}], manifest:{version:1, tests:[{id, criteria:[string], "
        "command:[string], timeout_seconds:int}]}}; `path` is relative to the suite root, `command` "
        "runs from the repository root with a minimal environment, use \"{python}\" for the interpreter. "
        "When payload.mode is 'smoke-maintenance' you are proposing an update to the PROJECT-LEVEL system smoke "
        "suite (payload.smoke_root) after the change merged: from payload.acceptance_suite pick only the change's "
        "core flow and must-not-break basics, merge them into payload.system_smoke (keep every existing test whose "
        "behaviour is still valid, update those the contract legitimately changed, never duplicate), and return "
        "{files:[{path,content}], manifest:{version:1, tests:[{id, description, command:[string], timeout_seconds}]}, "
        "rationale:string} where paths are relative to payload.smoke_root and commands run from the repository "
        "root; a human reviews and applies it. "
        "When payload.change.repair is present you are repairing your own suite: fix only the test defects "
        "shown in failure_evidence (locators, timing, data setup), keep every criterion covered, never weaken "
        "an assertion about product behavior, and return the complete suite (all files and the manifest). "
        "When failure_evidence.status is EXISTING_GREEN the suite passed on the code before implementation "
        "(criteria_green lists the criteria it failed to fail): strengthen those tests so they assert the "
        "new behaviour the contract requires and fail on the current code."
    ),
    "implement": (
        "Implement the approved change in this repository. Modify only new files under configured module "
        "roots. If an integration or foundation file must change, explain that need without editing it. "
        "payload.design_plan (when present) is the architect's plan: follow its Files, Data And Interfaces and "
        "Decisions; modify no existing file outside its `touched` list; if you must deviate, say exactly why in "
        "the summary. "
        + FOUNDATION_NOTE + "Follow extension.md for where a new feature's files go and reuse what "
        "building-blocks.md lists instead of re-implementing it. "
        "Never edit the foundation, OpenSpec contract, acceptance "
        "tests, reports, CI, or .storyloop. Do not commit or push. "
        "Work test-first: for each behaviour, write a unit test in the project's own test layout, run it "
        "and see it fail for the intended reason, write the minimal code that makes it pass, then refactor "
        "with the tests green. Run the project's regression command before you finish. "
        "payload.answers lists human decisions on earlier questions; follow them and never re-ask. "
        "Return exactly {summary:string,questions:[string],design:string}. `design` is a Markdown design "
        "record of what you actually built, with sections ## Context, ## Goals, ## Tests First (each test "
        "you wrote, the failure you observed before implementing, and the final green run), ## Decisions "
        "(each: Choice, Rationale, Alternatives considered), ## Risks And Trade-Offs, ## Files "
        "(path: responsibility); questions only for genuine product ambiguities."
    ),
    "review": (
        "Independently review the real implementation against the approved stories and acceptance evidence. "
        + FOUNDATION_NOTE + "Treat re-implementation of a listed building block, or a feature placed "
        "against extension.md, as a finding. "
        "payload.design_plan (when present) is the architect's plan: an implementation that departs from its "
        "Files, interfaces or decisions without a stated reason is a finding. "
        "payload.design is the implementer's design record; check that the unit tests it lists under "
        "## Tests First exist in the repository and cover the behaviours claimed, and treat a missing or "
        "hollow Tests First section as a finding. "
        "Do not edit files. Return exactly {verdict:'pass'|'fail'|'needs_input',summary:string,"
        "findings:[object],questions:[string]}. Pass only with no findings or questions."
    ),
    "design": (
        "You are the architect. The contract is approved and its acceptance suite already proved RED; "
        "before anyone writes code, produce the solution design. "
        + FOUNDATION_NOTE + "Follow extension.md for where each layer's files go, reuse what "
        "building-blocks.md lists, and respect modules.md's dependency direction. Read the existing code "
        "you intend to change. Return exactly {summary:string, touched:[string], design:string, questions:[string]}: "
        "`touched` is every EXISTING non-test file the implementation will modify (repository-relative; files "
        "that will merely be created are described in the design, not listed here); `design` is Markdown with "
        "## Context, ## Goals, ## Decisions (each: Choice, Rationale, Alternatives considered), ## Files "
        "(every file to create or modify with its responsibility), ## Data And Interfaces (tables, columns, "
        "endpoints, payloads, permission strings), ## Risks And Trade-Offs. Do not write any file. "
        "questions only for genuine decisions the contract and the base leave open (payload.answers holds "
        "earlier answers; never re-ask)."
    ),
    "characterize": (
        "Before any implementation, lock the existing behaviour of the code this change is about to modify. "
        + FOUNDATION_NOTE + "Read payload.change (the approved contract) and, using extension.md and "
        "building-blocks.md, decide which EXISTING non-test files the implementation will have to modify; list "
        "them in `touched` as repository-relative paths (files that will merely be added are not listed). For "
        "each touched path write characterization tests in the project's own unit-test layout (payload.design, "
        "when present, is the architect's plan: its `touched` list is mandatory and its Markdown says what will "
        "change, so lock what must stay) (only paths under "
        "payload.test_roots may be created or changed): assert only behaviour that MUST NOT change, use "
        "contains-style assertions for lists or fields that the change will extend, never assert the new "
        "behaviour. Run the tests: they must pass on the current code. payload.extend lists paths the engine "
        "found modified without a lock; when present, characterize exactly those (payload.characterization "
        "shows what is already locked). If nothing existing will change, return touched: [] and tests: []. "
        "Do not modify non-test files, do not commit. Return exactly {summary:string, touched:[string], "
        "tests:[string], questions:[]}."
    ),
    "survey": (
        "Survey this repository's foundation by following the foundation-survey skill below exactly, in "
        "its StoryLoop-engine mode: read-only, no questions, and the final answer is only one JSON object "
        "{documents:[{path,content}]} with `path` relative to payload.foundation_root covering every name in "
        "payload.required_documents. payload.module_roots is the mutable area and payload.integration_paths "
        "the build integration points; everything else is protected foundation."
    ),
}


SKILL_ROLES = {"survey": "foundation-survey"}
FOUNDATION_LEDGER = "foundation"  # survey calls are base work, billed apart from any change


def instructions_for(project: Path, role: str) -> str:
    """Role instructions; roles defined by a packaged skill embed the skill (project copy first)."""
    text_ = INSTRUCTIONS[role]
    if role in SKILL_ROLES:
        text_ += "\n\nSkill:\n" + skill_text(project, SKILL_ROLES[role])
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
                   repairs=trace["repairs"])


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
    require(bool(executable), "Agent executable is unavailable")
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
    raise StoryLoopError("Codex CLI is not available. Configure a manual or command adapter.")


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
        raise StoryLoopError(f"Project agent process failed safely ({detail})")


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
