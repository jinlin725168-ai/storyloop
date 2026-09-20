"""StoryLoop `command` adapter that drives Claude Code (`claude -p`) per role.

Usage in .storyloop/config.json:
  "adapter": "command",
  "agent_command": ["{python}", "/abs/path/claude_agent.py", "--bash", "mvn:*",
                    "--guide", "/abs/path/AGENT_GUIDE.md", "--model", "opus"]

stdin  : one StoryLoop request JSON (protocol, role, project, payload, instructions)
stdout : exactly one JSON object (the role result, plus `_telemetry` which StoryLoop strips)
Claude Code runs in --restricted print mode; tools are narrowed per role and any
permission that would prompt is denied instead of hanging.

Turn caps are checkpoints, not failures: each segment runs up to --segment-turns agentic
turns; when Claude stops with error_max_turns the same session is resumed (`--resume`) with
its full context, up to --max-segments. Whether the work is good enough is judged by
StoryLoop's attempt budget, not by how many turns it took.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

READ_ONLY = ["Read", "Grep", "Glob"]
ROLE_TOOLS = {
    "product": READ_ONLY,
    "smoke": READ_ONLY,
    "review": READ_ONLY + ["Bash"],
    "implement": READ_ONLY + ["Edit", "Write", "MultiEdit", "Bash"],
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
        "Exact shape: {version:1, goal:string, non_goals:[string], questions:[{id,text,options:[string]}], "
        "stories:[{id,title,as_a,want,so_that,rules:[string],out_of_scope:[string],depends_on:[string],"
        "acceptance:[{id,given,when,then,kind:'automated'|'manual'}]}]}. "
        "IDs like S1/A1/Q1 must be unique across the whole product. No extra fields anywhere."
    ),
    "smoke": (
        "Exact shape: {files:[{path,content}], manifest:{version:1, tests:[{id, criteria:[string], "
        "command:[string], timeout_seconds:int}]}}. The suite root is payload.suite_root; `path` is "
        "relative to it. `command` runs from the repository root with a minimal environment, so "
        "reference files as <suite_root>/<file> and use \"{python}\" for the interpreter. "
        "Every automated criterion id must appear in some test's criteria. No extra fields."
    ),
    "implement": "Exact shape: {summary:string, questions:[string]}.",
    "review": "Exact shape: {verdict:'pass'|'fail'|'needs_input', summary:string, findings:[object], questions:[string]}.",
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


def main() -> int:
    return main_with_args(None)


def main_with_args(argv: list[str] | None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=None)
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
    args = parser.parse_args(argv)
    segment_turns = args.max_turns or args.segment_turns

    request = json.load(sys.stdin)
    role = request.get("role")
    if role not in ROLE_TOOLS:
        print(f"unsupported role {role!r}", file=sys.stderr)
        return 2
    claude = shutil.which("claude")
    if not claude:
        print("claude executable not found on PATH", file=sys.stderr)
        return 2

    prompt = _prompt(request, role, args.guide)
    base = _base_command(claude, role, segment_turns, args.bash, args.model)
    if args.dry_run:
        print(json.dumps({"command": base, "prompt_chars": len(prompt)}))
        return 0

    telemetry = {"cost_usd": 0.0, "turns": 0, "duration_ms": 0, "input_tokens": 0,
                 "output_tokens": 0, "segments": 0, "models": set()}
    session = None
    for segment in range(1, args.max_segments + 1):
        command = base + (["--resume", session] if session else [])
        completed = subprocess.run(command, input=RESUME_PROMPT if session else prompt,
                                   capture_output=True, text=True, cwd=request.get("project"))
        # claude exits 1 on error_max_turns but still prints the JSON envelope on stdout, so the
        # envelope is authoritative and the exit code only matters when there is no envelope.
        try:
            envelope = json.loads(completed.stdout)
        except json.JSONDecodeError:
            print(f"claude exited {completed.returncode} without a JSON envelope: "
                  f"{completed.stderr[-4000:]}", file=sys.stderr)
            return completed.returncode or 1
        _accumulate(telemetry, envelope)
        session = envelope.get("session_id") or session
        subtype = envelope.get("subtype", "success")
        if subtype == "error_max_turns" and session:
            print(f"claude {role}: segment {segment} hit {segment_turns} turns, resuming {session}",
                  file=sys.stderr)
            continue
        if envelope.get("is_error") or subtype != "success":
            print(f"claude ended with {subtype}: {envelope.get('result')}", file=sys.stderr)
            return 1
        result = _extract_object(envelope.get("result", ""))
        if result is None:
            print("claude result was not a JSON object", file=sys.stderr)
            return 1
        result["_telemetry"] = _finish(telemetry)  # StoryLoop strips and records this
        print(f"claude {role}: cost=${telemetry['cost_usd']:.4f} turns={telemetry['turns']} "
              f"segments={telemetry['segments']}", file=sys.stderr)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    print(f"claude {role}: gave up after {args.max_segments} segments "
          f"({telemetry['turns']} turns); the agent is looping, not just slow", file=sys.stderr)
    return 1


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
    if role == "implement":
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
    if models:
        value["model"] = ",".join(models)
    return value


def _prompt(request: dict, role: str, guides: list[str]) -> str:
    parts = [request.get("instructions", ""), SCHEMA_HINTS[role], RULES]
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
