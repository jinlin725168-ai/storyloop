"""Human touches (roadmap G): one line per state-changing command a person typed on a change.

Recorded at the CLI entry, before the command runs, so the report written by that same command
already counts it. What the engine continues on its own (automatic repairs, network retries)
never appears here. The ledger lives in the control plane and is never committed.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

from .models import StoryLoopError
from .path_security import safe_target

LEDGER = Path(".storyloop/state/touches")
PROJECT_LEDGER = "_project"
# What kind of touch each command is; anything else (status, log, report, ...) only reads.
KINDS = {"approve": "decision", "answer": "decision", "accept": "decision",
         "authorize-knowledge": "decision", "base-review": "decision",
         "change": "draft", "run": "resume", "reset": "repair",
         "acceptance-repair": "repair", "smoke-repair": "repair"}
LABELS = {"decision": "决定", "draft": "草案修订", "resume": "续跑", "repair": "修复"}


def touch_kind(args) -> str | None:
    command = getattr(args, "command", None)
    if command == "change":
        if getattr(args, "supersede", None):
            return "decision"
        if not (getattr(args, "change_id", None) and getattr(args, "message", None)):
            return None
        return "decision" if _has_open_questions(args.project, args.change_id) else "draft"
    if command == "approve" and (getattr(args, "readiness", None) or getattr(args, "draft", None)):
        return "decision"  # project-level: readiness gaps and lesson drafts
    return KINDS.get(command)


def _has_open_questions(project: Path, change_id: str) -> bool:
    """A revision sent while questions are open answers them: that is a decision, not an edit."""
    try:
        state = json.loads((Path(project) / ".storyloop/state/changes" / f"{change_id}.json")
                           .read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return bool(isinstance(state, dict) and (state.get("product") or {}).get("questions"))


def record_touch(args) -> None:
    """Append one line for a state-changing human command; never fails the command itself."""
    kind = touch_kind(args)
    project = getattr(args, "project", None)
    if kind is None or not isinstance(project, Path):
        return
    try:
        project = project.resolve()
        if not (project / ".storyloop").is_dir():
            return  # not initialized (init itself is setup, not a touch on a change)
        change_id = getattr(args, "change_id", None) or getattr(args, "supersede", None)
        name = change_id or PROJECT_LEDGER
        target = safe_target(project, (LEDGER / f"{name}.jsonl").as_posix(), "touch ledger")
        target.parent.mkdir(parents=True, exist_ok=True)
        entry = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 "command": args.command, "kind": kind, "change_id": change_id}
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except (OSError, ValueError, StoryLoopError):
        return  # a ledger problem must never block the person's command


def touches(project: Path, change_id: str) -> dict | None:
    """Per-change touch counts by kind; None (unknown, not zero) for a change with no ledger,
    e.g. one that predates it or was driven without the CLI."""
    by_kind = {name: 0 for name in LABELS}
    path = Path(project) / LEDGER / f"{change_id}.jsonl"
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            kind = json.loads(line).get("kind")
        except ValueError:
            continue
        if kind in by_kind:
            by_kind[kind] += 1
    return {"total": sum(by_kind.values()), "by_kind": by_kind}


def render_touches(value: dict | None) -> str:
    if not value:
        return "人工触达：无记录"
    parts = "、".join(f"{LABELS[name]} {count}" for name, count in value["by_kind"].items() if count)
    return f"人工触达 {value['total']} 次" + (f"（{parts}）" if parts else "")
