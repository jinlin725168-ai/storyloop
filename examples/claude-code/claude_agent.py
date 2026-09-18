"""StoryLoop `command` adapter that drives Claude Code (`claude -p`) per role.

Usage in .storyloop/config.json:
  "adapter": "command",
  "agent_command": ["{python}", "/abs/path/claude_agent.py", "--bash", "mvn:*",
                    "--guide", "/abs/path/AGENT_GUIDE.md", "--model", "opus"]

stdin  : one StoryLoop request JSON (protocol, role, project, payload, instructions)
stdout : exactly one JSON object (the role result)
Claude Code runs in --restricted print mode; tools are narrowed per role and any
permission that would prompt is denied instead of hanging.
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
# Git is deliberately absent: `git status` rewrites .git/index, which StoryLoop treats as
# an ignored-file mutation (WORKSPACE_TAINTED).
DEFAULT_BASH = ["python3:*", "python:*", "ls:*", "cat:*", "head:*", "tail:*", "find:*",
                "grep:*", "wc:*", "diff:*"]
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
    "Never run git commands and never create Git-ignored files (build output, caches, logs): "
    "StoryLoop snapshots the whole tree including .git and fails the run on any such change.\n"
    "Shell commands are pre-approved only as plain single commands: no `export`, `&&`, `;` or "
    "pipes. Nobody can answer permission prompts, so never ask for approval; if a command is "
    "denied, rerun it as one plain command.\n"
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=None)
    parser.add_argument("--max-turns", type=int, default=60)
    parser.add_argument("--bash", action="append", default=[], help="extra Bash(prefix:*) allow rule")
    parser.add_argument("--guide", action="append", default=[],
                        help="project guidance file appended to every prompt (repeatable)")
    parser.add_argument("--dry-run", action="store_true", help="print the claude argv and exit")
    args = parser.parse_args()

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
    tools = ROLE_TOOLS[role]
    allowed = [tool for tool in tools if tool != "Bash"]
    if "Bash" in tools:
        allowed += [f"Bash({rule})" for rule in DEFAULT_BASH + args.bash]
    command = [
        claude, "-p", "--restricted", "--output-format", "json",
        "--permission-prompts", "none", "--max-turns", str(args.max_turns),
        "--tools", ",".join(tools), "--allowedTools", *allowed,
    ]
    if role == "implement":
        command += ["--permission-mode", "acceptEdits"]
    if args.model:
        command += ["--model", args.model]
    if args.dry_run:
        print(json.dumps({"command": command, "prompt_chars": len(prompt)}))
        return 0

    completed = subprocess.run(command, input=prompt, capture_output=True, text=True,
                               cwd=request.get("project"))
    if completed.returncode != 0:
        print(completed.stderr[-4000:], file=sys.stderr)
        return completed.returncode or 1
    try:
        envelope = json.loads(completed.stdout)
    except json.JSONDecodeError:
        print("claude did not return a JSON envelope", file=sys.stderr)
        return 1
    if envelope.get("is_error") or envelope.get("subtype", "success") != "success":
        print(f"claude ended with {envelope.get('subtype')}: {envelope.get('result')}", file=sys.stderr)
        return 1
    result = _extract_object(envelope.get("result", ""))
    if result is None:
        print("claude result was not a JSON object", file=sys.stderr)
        return 1
    print(f"claude {role}: cost=${envelope.get('total_cost_usd', 0):.4f} "
          f"turns={envelope.get('num_turns')}", file=sys.stderr)
    print(json.dumps(result, ensure_ascii=False))
    return 0


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
