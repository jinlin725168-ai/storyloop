"""Pre-change assessment (spec-craft batch 3, design §5.1): is this worth building?

Three questions answered in free text by a human — who has which problem (a real occurrence), how
we will know it is solved, what happens if we do not — then a human decision: do, clarify or drop.
A "drop" creates no change; that is a successful outcome. The record lives in the control plane
(.storyloop/assessments/) so writing it never dirties the tracked tree; a change created with
`--assessment <id>` copies it into its co-creation record.
"""
from __future__ import annotations

from datetime import datetime, timezone
import re
import shlex
import sys
from pathlib import Path

from .models import StoryLoopError, require, text
from .storage import load_json, write_json

ROOT = Path(".storyloop/assessments")
QUESTIONS = (("problem", "--problem", "谁遇到了什么问题？请讲一次真实发生的情况"),
             ("success", "--success", "做完之后，凭什么知道问题解决了？"),
             ("if_not", "--if-not", "不做会怎样？"))
DECISIONS = ("do", "clarify", "drop")


def create_assessment(project: Path, idea: str, answers: dict, decision: str, *,
                      now=None) -> dict:
    """Validate and store one assessment; returns its record (with `id`)."""
    text(idea, "idea")
    for key, flag, _ in QUESTIONS:
        require(isinstance(answers.get(key), str) and answers[key].strip(),
                f"Assessment needs {flag}: the human's own answer, not an AI summary")
    require(decision in DECISIONS, "Assessment --decision is do, clarify or drop")
    moment = (now or (lambda: datetime.now(timezone.utc)))()
    slug = re.sub(r"[^a-z0-9]+", "-", idea.lower()).strip("-")[:30].strip("-") or "idea"
    identifier = _unique(Path(project), f"ASSESS-{moment:%Y%m%d}-{slug}")
    record = {"version": 1, "id": identifier, "idea": idea.strip(),
              "answers": {key: answers[key].strip() for key, _, _ in QUESTIONS},
              "decision": decision, "decided_by": "human",
              "at": moment.isoformat(timespec="seconds")}
    write_json(Path(project) / ROOT / f"{identifier}.json", record, max_bytes=64_000)
    return record


def load_assessment(project: Path, identifier: str) -> dict:
    require(re.fullmatch(r"ASSESS-\d{8}-[a-z0-9-]+", identifier or "") is not None,
            "Invalid assessment id")
    path = Path(project) / ROOT / f"{identifier}.json"
    require(path.is_file(), f"Assessment {identifier} does not exist")
    return load_json(path)


def require_do(project: Path, identifier: str) -> dict:
    """Only a "do" decision may start a change."""
    record = load_assessment(project, identifier)
    if record["decision"] != "do":
        raise StoryLoopError(f"Assessment {identifier} decision is {record['decision']}: "
                             "only do starts a change")
    return record


def _unique(project: Path, base: str) -> str:
    candidate, number = base, 2
    while (project / ROOT / f"{candidate}.json").exists():
        candidate, number = f"{base}-{number}", number + 1
    return candidate


def assess_command(project: Path, args) -> dict:
    """Ask the three questions in free text when not given as flags, then record the decision."""
    interactive = not args.no_interactive and sys.stdin.isatty()
    answers = {}
    for key, flag, prompt in QUESTIONS:
        value = getattr(args, key)
        if not value and interactive:
            value = input(prompt + "\n> ").strip()
        answers[key] = value
    decision = args.decision
    if not decision and interactive:
        decision = input("结论：do（做）/ clarify（需澄清）/ drop（放弃）\n> ").strip()
    record = create_assessment(project, args.idea, answers, decision or "")
    where = shlex.join([str(project)])
    next_step = {"do": f"storyloop change {where} --idea {shlex.quote(args.idea)} --assessment {record['id']}",
                 "clarify": "先把待澄清的事项弄清楚，再重新 storyloop assess",
                 "drop": "已放弃：不创建变更，评估记录保留在 .storyloop/assessments/"}[record["decision"]]
    return {"status": "ASSESSED", "assessment_id": record["id"], "decision": record["decision"],
            "next": next_step}
