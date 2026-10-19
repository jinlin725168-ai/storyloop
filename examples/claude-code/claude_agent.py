"""StoryLoop `command` adapter that drives Claude Code (`claude -p`) per role.

Usage in .storyloop/config.json:
  "adapter": "command",
  "agent_command": ["{python}", "/abs/path/claude_agent.py", "--bash", "mvn:*",
                    "--guide", "/abs/path/AGENT_GUIDE.md", "--model", "opus"]

stdin  : one StoryLoop request JSON (protocol, role, project, payload, instructions)
stdout : exactly one JSON object (the role result, plus `_telemetry` which StoryLoop strips)
Claude Code runs in --restricted print mode; tools are narrowed per role and any
permission that would prompt is denied instead of hanging.

"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
import subprocess
import sys

from storyloop.adapter_profiles import (model_for, tier_models, cli_capabilities,
                                       sequential_profile, sequential_instructions)

READ_ONLY = ["Read", "Grep", "Glob"]
ROLE_TOOLS = {
    "product": READ_ONLY,
    "acceptance": READ_ONLY,
    "smoke": READ_ONLY,  # pre-rename engines
    "review": READ_ONLY + ["Bash"],
    "implement": READ_ONLY + ["Edit", "Write", "MultiEdit", "Bash"],
    "characterize": READ_ONLY + ["Edit", "Write", "MultiEdit", "Bash"],
    "design": READ_ONLY + ["Bash"],
    "survey": READ_ONLY + ["Bash"],
}
# Bash prefixes pre-approved for roles that may run commands; extend per project with
# `--bash "mvn:*"`. Claude Code checks every segment of a chained command against these
# rules, so the prompt tells the agent to run plain single commands (no export/&&/pipes).
# Read-only git is allowed (StoryLoop tolerates the .git/index refresh it causes); anything
# that stages, commits or switches branches is detected and fails the run.
DEFAULT_BASH = ["python3:*", "python:*", "ls:*", "cat:*", "head:*", "tail:*", "find:*",
                "grep:*", "wc:*", "diff:*", "git status:*", "git diff:*", "git log:*", "git show:*"]
SCHEMA_HINTS = {
    "product": (
        "Exact shape: {version:1, goal:string, non_goals:[string], capability?:slug, "
        "clarifications?:[{session,question,answer}], questions:[{id,text,options:[string],"
        "recommended?:string,why?:string}], stories:[{id,title,priority:'P1'|'P2'|'P3',"
        "independent_test:string,as_a,want,so_that,rules:[string],out_of_scope:[string],"
        "depends_on:[string],acceptance:[{id,given,when,then,kind:'automated'|'manual'}]}]}. "
        "IDs like S1/A1/Q1 must be unique across the whole product. No extra fields anywhere."
    ),
    "acceptance": (
        "Exact shape: {files:[{path,content}], manifest:{version:1, tests:[{id, criteria:[string], "
        "command:[string], timeout_seconds:int}]}}. The suite root is payload.suite_root; `path` is "
        "relative to it. `command` runs from the repository root with a minimal environment, so "
        "reference files as <suite_root>/<file> and use \"{python}\" for the interpreter. "
        "Every automated criterion id must appear in some test's criteria. No extra fields."
    ),
    "implement": ("Exact shape: {summary:string, questions:[string], design:string}; design is Markdown with "
                  "## Context, ## Goals, ## Tests First (each test: observed failure, then green), "
                  "## Decisions (Choice/Rationale/Alternatives), ## Risks And Trade-Offs, ## Files."),
    "characterize": "Exact shape: {summary:string, touched:[string], tests:[string], questions:[]}; no extra fields.",
    "design": ("Exact shape: {summary:string, touched:[string], design:string, questions:[string]}; design is Markdown with "
               "## Context, ## Goals, ## Decisions, ## Files, ## Data And Interfaces, ## Risks And Trade-Offs. No extra fields."),
    "review": "Exact shape: {verdict:'pass'|'fail'|'needs_input', summary:string, findings:[object], questions:[string]}.",
    "survey": ("Exact shape: {documents:[{path,content}]}; path relative to payload.foundation_root, Markdown only, "
               "every name in payload.required_documents present. No extra fields."),
}
RULES = (
    "Final answer must be ONLY one JSON object, no markdown fences, no prose.\n"
    "Only read-only git (status/diff/log/show); never add, commit, stash or switch branches. "
    "Do not create Git-ignored files (build output, caches, logs) except under the project's "
    "declared scratch paths: StoryLoop snapshots the whole tree and fails the run otherwise.\n"
    "Shell commands are pre-approved only as plain single commands: no `export`, `&&`, `;` or "
    "pipes. Nobody can answer permission prompts, so never ask for approval; if a command is "
    "denied, rerun it as one plain command.\n"
    "Questions are only for genuine product ambiguities. Never ask about tooling, permissions or "
    "the environment: skip the affected check, say so in the summary, and let the external "
    "acceptance run verify the result.\n"
    "payload.answers lists human decisions on questions raised in earlier attempts "
    "({attempt, question, answer}); follow them and never ask an answered question again.\n"
)
RESUME_PROMPT = (
    "继续完成上一条请求中尚未完成的工作。已经写入磁盘的文件都还在，不要重做已完成的部分。"
    "完成后仍然只输出一个 JSON 对象。"
)
REPAIR_PROMPT = (
    "上一条回复不是一个 JSON 对象。工作已经完成的话不要重做，现在只输出那个 JSON 对象："
    "不要 markdown 围栏，不要任何解释文字。"
)
REPAIR_LIMIT = 2  # a finished session that ended in prose is asked for its JSON, like the Codex adapter

# Review fans out to focused perspectives (parallel sessions, same evidence) and a lead merges them.
# The engine protocol is untouched: it still receives one {verdict, summary, findings, questions}.
REVIEW_PERSPECTIVES = {
    "correctness": (
        "Focus only on correctness: walk every automated and manual acceptance criterion of the contract "
        "against the code and the layer evidence; check edge cases, error handling, data consistency between "
        "list/detail/export paths, and that nothing outside the stories was changed."
    ),
    "security": (
        "Focus only on security: injection (SQL built from strings, unbound parameters), authorization "
        "(permission strings on every endpoint, ownership checks), sensitive data in logs or responses, "
        "input validation, secrets or credentials in code or config. Cite file and line for every finding."
    ),
    "architecture": (
        "Focus only on conformance to the base and the plan: payload.design_plan (if present), the foundation "
        "documents extension.md, building-blocks.md and modules.md. Re-implemented building blocks, files in "
        "the wrong layer, forbidden dependency directions, or deviations from the plan without a stated reason "
        "are findings."
    ),
    "tests": (
        "Focus only on test quality: the implementer's design record ## Tests First must match real test "
        "files that exercise the claimed behaviours; characterization tests must cover every touched path; "
        "assertions must be meaningful (no tautologies, no tests that pass without the change)."
    ),
}
# Implement can split a full-stack change into per-layer lanes: a lead session plans the lanes from the
# architect's design_plan, lane sessions work in parallel on disjoint path prefixes, and the same lead
# session (resumed, so it keeps the plan in context) integrates, runs the checks and writes the record.
LANE_PLAN_MARK = "[implement lead: plan lanes]"
LANE_MARK = "[implement lane: {name}]"
LANE_INTEGRATE_MARK = "[implement lead: integrate]"
LANE_PLAN_RULES = (
    "You are the lead implementer. Do NOT write any file in this step. Read payload.design_plan and decide "
    "which of the configured lanes have work in this change; each lane is one layer owned by one "
    "parallel implementer. Return ONLY {lanes:[{name:string, task:string}]} using only configured lane "
    "names; `task` is a precise brief for that lane (files to create or modify, interfaces to expose or "
    "consume, tests to write first). Lanes that have nothing to do are omitted."
)
LANE_RULES = (
    "You are one lane of a parallel implementation. Only create or modify files under the lane's path "
    "prefixes listed below; other lanes are editing other paths right now, so never touch them and never "
    "run project-wide formatters, git commands that stage or the full regression. Work test-first within "
    "your layer. Return ONLY {summary:string, files:[string], questions:[]}: `files` lists every path you "
    "created or modified."
)
LANE_INTEGRATE_RULES = (
    "Every lane has finished; their reports follow. Integrate: reconcile interfaces between layers, run the "
    "project's build and unit/integration commands, fix whatever breaks (in any lane's files), and then "
    "return the final implement result exactly as {summary:string, questions:[string], design:string}; "
    "the design's ## Tests First must cover the tests of every lane."
)


def _strict(properties: dict) -> dict:
    return {"type": "object", "properties": properties, "required": list(properties),
            "additionalProperties": False}

_TEXT = {"type": "string"}
_TEXTS = {"type": "array", "items": _TEXT}
PERSPECTIVE_OUTPUT = _strict({"summary": _TEXT, "findings": {"type": "array", "items": _strict(
    {"message": _TEXT, "evidence": _TEXT, "severity": {"type": "string", "enum": ["P0", "P1", "P2", "question"]},
     "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
     "introduced": {"type": "boolean"}, "trigger": _TEXT, "impact": _TEXT, "fix": _TEXT})}, "questions": _TEXTS})
LANE_PLAN_OUTPUT = _strict({"lanes": {"type": "array", "items": _strict({"name": _TEXT, "task": _TEXT})}})
LANE_OUTPUT = _strict({"summary": _TEXT, "files": _TEXTS, "questions": _TEXTS})
PERSPECTIVE_MARK = "[review perspective: {name}]"
LEAD_MARK = "[lead reviewer]"
PERSPECTIVE_SCHEMA = (
    "Return summary, questions and findings with message, evidence, severity P0/P1/P2/question, "
    "confidence 0-100, introduced boolean, trigger, impact and fix. A finding "
    "must name a concrete defect you can point to (file, line or evidence id); doubts without evidence go "
    "into summary, not findings. questions only for genuine product ambiguities."
)
LEAD_RULES = (
    "You are the lead reviewer. Below are the reports of focused reviewers who all saw the same request. "
    "Merge them into the final review: verdict is 'fail' if any retained finding remains, 'needs_input' if "
    "there are no findings but open questions, otherwise 'pass'. You may drop a reviewer's finding only "
    "by verifying it yourself and stating the reason in the summary; never drop it silently. Deduplicate, "
    "keep each retained finding's perspective, message, severity, confidence, introduced, trigger, impact "
    "and fix. Include coverage and the requested decision checklist. Give a one-line conclusion per "
    "perspective in the summary. Return ONLY {verdict:'pass'|'fail'|'needs_input', summary:string, "
    "findings:[{perspective,message,severity,confidence,introduced,trigger,impact,fix}], "
    "coverage:[string], checklist:[{decision,status}], questions:[string]}."
)

def main() -> int:
    return main_with_args(None)


def _arguments(argv: list[str] | None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=None)
    parser.add_argument("--tier-model", action="append", default=[])
    parser.add_argument("--segment-turns", type=int, default=80,
                        help="agentic turns per claude -p segment before the session is resumed")
    parser.add_argument("--max-segments", type=int, default=10,
                        help="hard stop against runaway loops (total turns = segments x segment turns)")
    parser.add_argument("--max-turns", type=int, default=None,
                        help="deprecated alias for --segment-turns")
    parser.add_argument("--bash", action="append", default=[], help="extra Bash(prefix:*) allow rule")
    parser.add_argument("--guide", action="append", default=[],
                        help="project guidance file appended to every prompt (repeatable)")
    parser.add_argument("--dry-run", action="store_true", help="print the claude argv and exit")
    parser.add_argument("--review-perspectives", default=",".join(REVIEW_PERSPECTIVES),
                        help="comma-separated review perspectives run as parallel sub-agents, or 'none'")
    parser.add_argument("--review-parallel", type=int, default=4, help="max concurrent perspective sessions")
    parser.add_argument("--implement-lanes", default="",
                        help="per-layer implement lanes as name=path-prefix,name=path-prefix (needs a design_plan)")
    parser.add_argument("--implement-parallel", type=int, default=3, help="max concurrent lane sessions")
    return parser.parse_args(argv)


def main_with_args(argv: list[str] | None) -> int:
    args = _arguments(argv)
    segment_turns = args.max_turns or args.segment_turns

    request = json.load(sys.stdin)
    try:
        models = tier_models(args.tier_model)
        model = model_for(request, models)
    except (ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 2
    if request.get("protocol") == "storyloop.adapter-capabilities.v1":
        print(json.dumps(cli_capabilities(models, "anthropic")))
        return 0
    role = request.get("role")
    if role not in ROLE_TOOLS:
        print(f"unsupported role {role!r}", file=sys.stderr)
        return 2
    claude = shutil.which("claude")
    if not claude:
        print("claude executable not found on PATH", file=sys.stderr)
        return 2

    prompt = _prompt(request, role, args.guide)
    base = _base_command(claude, role, segment_turns, args.bash, model or args.model)
    if args.dry_run:
        print(json.dumps({"command": base, "prompt_chars": len(prompt)}))
        return 0

    perspectives = [p for p in args.review_perspectives.split(",") if p and p != "none"]
    if role == "review" and perspectives and "role_profile" not in request:
        return _review_with_perspectives(request, base, args, perspectives)
    lanes = _parse_lanes(args.implement_lanes)
    if role == "implement" and lanes and "role_profile" not in request and (request.get("payload") or {}).get("design_plan"):
        return _implement_with_lanes(request, base, prompt, args, lanes)

    result, telemetry, _ = _run_session(base, prompt, role, args, request.get("project"),
                                        schema=request.get("response_schema"))
    if result is None:
        return 1
    telemetry.update(sequential_profile(request, "anthropic", model))
    return _emit(result, telemetry, role)


def _emit(result: dict, telemetry: dict, role: str) -> int:
    result["_telemetry"] = _finish(telemetry)  # StoryLoop strips and records this
    print(f"claude {role}: cost=${telemetry['cost_usd']:.4f} turns={telemetry['turns']} "
          f"segments={telemetry['segments']}", file=sys.stderr)
    print(json.dumps(result, ensure_ascii=False))
    return 0


def _new_telemetry() -> dict:
    return {"cost_usd": 0.0, "turns": 0, "duration_ms": 0, "input_tokens": 0,
            "output_tokens": 0, "segments": 0, "models": set(), "sessions": []}


def _run_session(base: list[str], prompt: str, role: str, args, cwd,
                 session: str | None = None, schema: dict | None = None) -> tuple[dict | None, dict, str | None]:
    """One claude session (optionally resuming `session`): resume on turn caps, ask for JSON on prose
    endings; `schema` makes the CLI enforce the answer's shape. Returns (result, telemetry, session_id)."""
    telemetry = _new_telemetry()
    next_prompt = prompt
    repairs = 0
    structured = ["--json-schema", json.dumps(schema, ensure_ascii=False)] if schema else []
    for segment in range(1, args.max_segments + 1):
        command = base + structured + (["--resume", session] if session else [])
        completed = subprocess.run(command, input=next_prompt, capture_output=True, text=True, cwd=cwd)
        next_prompt = RESUME_PROMPT
        # claude exits 1 on error_max_turns but still prints the JSON envelope on stdout, so the
        # envelope is authoritative and the exit code only matters when there is no envelope.
        try:
            envelope = json.loads(completed.stdout)
        except json.JSONDecodeError:
            print(f"claude {role} exited {completed.returncode} without a JSON envelope: "
                  f"{completed.stderr[-4000:]}", file=sys.stderr)
            return None, telemetry, session
        _accumulate(telemetry, envelope)
        session = envelope.get("session_id") or session
        if session and session not in telemetry["sessions"]:
            telemetry["sessions"].append(session)
        subtype = envelope.get("subtype", "success")
        if subtype == "error_max_turns" and session:
            print(f"claude {role}: segment {segment} hit the turn cap, resuming {session}", file=sys.stderr)
            continue
        if envelope.get("is_error") or subtype != "success":
            print(f"claude {role} ended with {subtype}: {envelope.get('result')}", file=sys.stderr)
            return None, telemetry, session
        structured_output = envelope.get("structured_output")
        result = structured_output if isinstance(structured_output, dict) else \
            _extract_object(envelope.get("result", ""))
        if result is None:
            if session and repairs < REPAIR_LIMIT:
                repairs += 1
                next_prompt = REPAIR_PROMPT
                print(f"claude {role}: result was not JSON, asking session {session} for it "
                      f"(repair {repairs}/{REPAIR_LIMIT})", file=sys.stderr)
                continue
            print(f"claude {role}: result was not a JSON object", file=sys.stderr)
            return None, telemetry, session
        return result, telemetry, session
    print(f"claude {role}: gave up after {args.max_segments} segments "
          f"({telemetry['turns']} turns); the agent is looping, not just slow", file=sys.stderr)
    return None, telemetry, session


def _review_with_perspectives(request: dict, base: list[str], args, perspectives: list[str]) -> int:
    """Parallel focused reviewers, then a lead session that merges their reports into one verdict."""
    unknown = [p for p in perspectives if p not in REVIEW_PERSPECTIVES]
    if unknown:
        print(f"unknown review perspectives: {unknown}", file=sys.stderr)
        return 2
    cwd = request.get("project")

    def run_perspective(name: str) -> tuple[str, dict | None, dict]:
        extra = f"{PERSPECTIVE_MARK.format(name=name)}\n{REVIEW_PERSPECTIVES[name]}\n{PERSPECTIVE_SCHEMA}"
        prompt = _prompt(request, "review", args.guide, extra=extra, schema=False)
        result, telemetry, _ = _run_session(base, prompt, f"review/{name}", args, cwd,
                                            schema=PERSPECTIVE_OUTPUT)
        return name, result, telemetry

    with ThreadPoolExecutor(max_workers=max(1, args.review_parallel)) as pool:
        reports = list(pool.map(run_perspective, perspectives))
    total = _new_telemetry()
    for _, _, telemetry in reports:
        _merge_telemetry(total, telemetry)
    failed = [name for name, result, _ in reports if result is None]
    if failed:
        print(f"claude review: perspective(s) failed: {', '.join(failed)}", file=sys.stderr)
        return 1
    digest = json.dumps({name: result for name, result, _ in reports}, ensure_ascii=False, indent=1)
    lead_prompt = _prompt(request, "review", args.guide,
                          extra=f"{LEAD_MARK}\n{LEAD_RULES}\n\nPerspective reports:\n{digest}", schema=False)
    result, telemetry, _ = _run_session(base, lead_prompt, "review/lead", args, cwd,
                                        schema=request.get("response_schema"))
    _merge_telemetry(total, telemetry)
    if result is None:
        return 1
    if not ({"verdict", "summary", "findings", "questions"} <= set(result)):
        print("claude review: lead result lacks verdict/summary/findings/questions", file=sys.stderr)
        return 1
    result = {key: result[key] for key in ("verdict", "summary", "findings", "questions", "coverage", "checklist")
              if key in result}
    total["perspectives"] = len(perspectives)
    return _emit(result, total, "review")


def _parse_lanes(value: str) -> dict[str, str]:
    lanes: dict[str, str] = {}
    for item in [v for v in value.split(",") if v.strip()]:
        name, separator, prefix = item.partition("=")
        if not separator or not name.strip() or not prefix.strip():
            raise SystemExit(f"invalid --implement-lanes entry: {item!r} (expected name=path-prefix)")
        lanes[name.strip()] = prefix.strip()
    return lanes


def _implement_with_lanes(request: dict, base: list[str], single_prompt: str, args,
                          lanes: dict[str, str]) -> int:
    """Lead plans the lanes, lanes implement their layers in parallel, the lead integrates."""
    cwd = request.get("project")
    total = _new_telemetry()
    configured = "\n".join(f"- {name}: {prefix}" for name, prefix in lanes.items())
    plan_prompt = _prompt(request, "implement", args.guide,
                          extra=f"{LANE_PLAN_MARK}\n{LANE_PLAN_RULES}\nConfigured lanes:\n{configured}",
                          schema=False)
    plan, telemetry, lead = _run_session(base, plan_prompt, "implement/lead", args, cwd,
                                         schema=LANE_PLAN_OUTPUT)
    _merge_telemetry(total, telemetry)
    if plan is None:
        return 1
    chosen = [item for item in (plan.get("lanes") or []) if isinstance(item, dict)
              and item.get("name") in lanes and isinstance(item.get("task"), str)]
    if len(chosen) < 2:
        print(f"claude implement: {len(chosen)} lane(s) planned, running as one session", file=sys.stderr)
        result, telemetry, _ = _run_session(base, single_prompt, "implement", args, cwd,
                                            schema=request.get("response_schema"))
        _merge_telemetry(total, telemetry)
        return 1 if result is None else _emit(result, total, "implement")

    def run_lane(item: dict) -> tuple[str, dict | None, dict]:
        name = item["name"]
        extra = (f"{LANE_MARK.format(name=name)}\n{LANE_RULES}\nOnly create or modify files under: "
                 f"{lanes[name]}\nYour task:\n{item['task']}")
        prompt = _prompt(request, "implement", args.guide, extra=extra, schema=False)
        result, telemetry, _ = _run_session(base, prompt, f"implement/{name}", args, cwd,
                                            schema=LANE_OUTPUT)
        return name, result, telemetry

    with ThreadPoolExecutor(max_workers=max(1, args.implement_parallel)) as pool:
        reports = list(pool.map(run_lane, chosen))
    for _, _, telemetry in reports:
        _merge_telemetry(total, telemetry)
    failed = [name for name, result, _ in reports if result is None]
    if failed:
        print(f"claude implement: lane(s) failed: {', '.join(failed)}", file=sys.stderr)
        return 1
    digest = json.dumps({name: result for name, result, _ in reports}, ensure_ascii=False, indent=1)
    integrate_prompt = (f"{LANE_INTEGRATE_MARK}\n{LANE_INTEGRATE_RULES}\n\nLane reports:\n{digest}\n"
                        f"{SCHEMA_HINTS['implement']}")
    result, telemetry, _ = _run_session(base, integrate_prompt, "implement/lead", args, cwd, session=lead,
                                        schema=request.get("response_schema"))
    _merge_telemetry(total, telemetry)
    if result is None:
        return 1
    if not ({"summary", "questions", "design"} <= set(result)):
        print("claude implement: lead result lacks summary/questions/design", file=sys.stderr)
        return 1
    result = {key: result[key] for key in ("summary", "questions", "design")}
    total["lanes"] = len(chosen)
    return _emit(result, total, "implement")


def _merge_telemetry(total: dict, part: dict) -> None:
    for key in ("cost_usd", "turns", "duration_ms", "input_tokens", "output_tokens", "segments"):
        total[key] += part.get(key, 0)
    total["models"].update(part.get("models", set()))
    total["sessions"].extend(item for item in part.get("sessions", []) if item not in total["sessions"])


def _base_command(claude: str, role: str, segment_turns: int, extra_bash: list[str],
                  model: str | None) -> list[str]:
    tools = ROLE_TOOLS[role]
    allowed = [tool for tool in tools if tool != "Bash"]
    if "Bash" in tools:
        allowed += [f"Bash({rule})" for rule in DEFAULT_BASH + extra_bash]
    command = [
        claude, "-p", "--restricted", "--output-format", "json",
        "--permission-prompts", "none", "--max-turns", str(segment_turns),
        "--tools", ",".join(tools), "--allowedTools", *allowed,
    ]
    if role in {"implement", "characterize"}:
        command += ["--permission-mode", "acceptEdits"]
    if model:
        command += ["--model", model]
    return command


def _accumulate(telemetry: dict, envelope: dict) -> None:
    usage = envelope.get("usage") or {}
    telemetry["cost_usd"] += float(envelope.get("total_cost_usd") or 0)
    telemetry["turns"] += int(envelope.get("num_turns") or 0)
    telemetry["duration_ms"] += int(envelope.get("duration_ms") or 0)
    telemetry["input_tokens"] += int((usage.get("input_tokens") or 0)
                                     + (usage.get("cache_read_input_tokens") or 0)
                                     + (usage.get("cache_creation_input_tokens") or 0))
    telemetry["output_tokens"] += int(usage.get("output_tokens") or 0)
    telemetry["segments"] += 1
    telemetry["models"].update(name for name in (envelope.get("modelUsage") or {})
                               if "haiku" not in name)


def _finish(telemetry: dict) -> dict:
    value = dict(telemetry)
    models = sorted(value.pop("models"))
    value["cost_usd"] = round(value["cost_usd"], 6)
    value["duration_ms"] = int(value["duration_ms"])
    if models:
        value["model"] = ",".join(models)
    return value


def _prompt(request: dict, role: str, guides: list[str], extra: str = "", schema: bool = True) -> str:
    parts = [request.get("instructions", "") + sequential_instructions(request)]
    if extra:
        parts.append(extra)
    if schema:
        parts.append(SCHEMA_HINTS.get(role) or SCHEMA_HINTS["acceptance"])
    parts.append(RULES)
    for guide in guides:
        path = Path(guide)
        if path.is_file():
            parts.append(f"Project guidance ({path.name}):\n{path.read_text(encoding='utf-8')}")
        else:
            print(f"guide file not found, ignored: {guide}", file=sys.stderr)
    parts.append("Request:\n" + json.dumps(request, ensure_ascii=False))
    return "\n".join(parts)


def _extract_object(text: str):
    """Parse the whole text, else the outermost {...} span (tolerates stray prose)."""
    for candidate in (text, text[text.find("{"):text.rfind("}") + 1]):
        try:
            value = json.loads(candidate)
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict):
            return value
    return None


if __name__ == "__main__":
    sys.exit(main())
