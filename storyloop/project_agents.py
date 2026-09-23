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
from .foundation_survey import foundation_payload
from .skills import skill_text
from .processutil import redact, run_process
from .storage import load_json, write_json
from .telemetry import record_call

ROLES = {"product", "smoke", "implement", "review", "survey"}
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
    "smoke": (
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
        "When payload.change.repair is present you are repairing your own suite: fix only the test defects "
        "shown in failure_evidence (locators, timing, data setup), keep every criterion covered, never weaken "
        "an assertion about product behavior, and return the complete suite (all files and the manifest)."
    ),
    "implement": (
        "Implement the approved change in this repository. Modify only new files under configured module "
        "roots. If an integration or foundation file must change, explain that need without editing it. "
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
        "payload.design is the implementer's design record; check that the unit tests it lists under "
        "## Tests First exist in the repository and cover the behaviours claimed, and treat a missing or "
        "hollow Tests First section as a finding. "
        "Do not edit files. Return exactly {verdict:'pass'|'fail'|'needs_input',summary:string,"
        "findings:[object],questions:[string]}. Pass only with no findings or questions."
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
    """Invoke one stateless agent using the repository-local adapter configuration."""
    project = Path(project).resolve()
    require(role in ROLES, "Unknown project agent role")
    require(isinstance(payload, dict), "Agent payload must be an object")
    config = load_project_config(project)
    foundation = foundation_payload(project) if role != "survey" else None
    if foundation and "foundation" not in payload:
        payload = {**payload, "foundation": foundation}
    request = {
        "protocol": "storyloop.project-agent.v1",
        "role": role,
        "project": str(project),
        "payload": payload,
    }
    _bounded_json(request)
    adapter = config.get("adapter")
    started = time.monotonic()
    failed = True
    result = None
    try:
        if adapter == "manual":
            result = _manual(project, request)
        elif adapter == "command":
            result = _command(project, request, config)
        else:
            require(adapter == "codex", "Unknown project agent adapter")
            result = _codex(project, request, config)
        failed = False
        return result
    finally:
        telemetry = result.pop("_telemetry", None) if isinstance(result, dict) else None
        # A manual adapter waiting for input is not a failed call; everything else that raised is.
        record_call(project, role, FOUNDATION_LEDGER if role == "survey" else _change_id(payload), telemetry,
                    int((time.monotonic() - started) * 1000), failed=failed and adapter != "manual")


def _change_id(payload: dict) -> str | None:
    value = payload.get("change_id")
    if not isinstance(value, str):
        change = payload.get("change")
        value = change.get("change_id") if isinstance(change, dict) else None
    return value if isinstance(value, str) and value else None


def _manual(project: Path, request: dict) -> dict:
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
        input_text=json.dumps({**request, "instructions": instructions_for(project, request["role"])},
                              ensure_ascii=False),
    )
    _check_process(result)
    return _parse(result.get("stdout", ""))


CODEX_REPAIR_LIMIT = 2                 # resumes allowed to turn a prose ending into JSON
CODEX_MAX_OUTPUT = 16 * 1024 * 1024    # --json streams every event; far above the default cap
CODEX_REPAIR_PROMPT = (
    "上一条回复不是合法的 JSON 对象。不要再修改任何文件，也不要重复说明；"
    "只按原始要求输出一个 JSON 对象。"
)


def _codex(project: Path, request: dict, config: dict) -> dict:
    """Run Codex in a persistent session; resume it to repair a non-JSON final message.

    Codex has no turn cap, so a long task simply runs; the recoverable failure is the agent
    ending in prose. Sessions are kept on disk (no --ephemeral) so `codex exec resume` can
    finish the same thread with its context intact. Usage comes from the --json event stream.
    """
    sandbox = "workspace-write" if request["role"] == "implement" else "read-only"
    common = ["--ignore-user-config", "--color", "never", "--json"]
    prompt = instructions_for(project, request["role"]) + "\nRequest:\n" + json.dumps(request, ensure_ascii=False)
    telemetry = {"turns": 0, "input_tokens": 0, "output_tokens": 0, "segments": 0}
    thread: str | None = None
    for repair in range(CODEX_REPAIR_LIMIT + 1):
        if repair == 0:
            argv = _codex_executable() + ["exec", "--sandbox", sandbox, "-C", str(project), *common]
        else:
            argv = _codex_executable() + ["exec", "resume", thread, *common]
        content = _codex_once(project, argv, CODEX_REPAIR_PROMPT if repair else prompt,
                              config["agent_timeout_seconds"], telemetry)
        thread = telemetry.pop("thread", None) or thread
        try:
            result = _parse(content)
        except StoryLoopError:
            if not thread or repair == CODEX_REPAIR_LIMIT:
                raise
            continue
        result["_telemetry"] = dict(telemetry)
        return result
    raise StoryLoopError("Project agent did not return a JSON object")  # unreachable


def _codex_once(project: Path, argv: list[str], prompt: str, timeout: int, telemetry: dict) -> str:
    with tempfile.NamedTemporaryFile(dir=project / ".storyloop", suffix=".response",
                                     delete=False) as handle:
        output = Path(handle.name)
    try:
        result = run_process(argv + ["--output-last-message", str(output), "-"], project, timeout,
                             input_text=prompt, max_output=CODEX_MAX_OUTPUT)
        _check_process(result)
        _codex_events(result.get("stdout", ""), telemetry)
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
