"""Roadmap G, attention budget: list pending decisions across projects, notify only then.

Touches are counted in `touch_ledger`; this module is the command-level surface.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess

from .models import StoryLoopError
from .touch_ledger import record_touch  # noqa: F401  # re-exported for the CLI entry

def add_inbox_arguments(sub) -> None:
    child = sub.add_parser("inbox", help="跨变更（可跨多个项目）列出待你决定的事项与下一步命令")
    child.add_argument("projects", type=Path, nargs="+")
    child.add_argument("--json", action="store_true", help="输出完整 JSON")


def inbox(projects: list[Path], draft_result) -> dict:
    """Every pending human decision across changes (and projects), oldest first."""
    from .status_overview import overview
    items = []
    for project in projects:
        project = Path(project).resolve()
        if not (project / ".storyloop").is_dir():
            continue
        from .workers import workers_status
        workers = {row["change_id"]: row for row in workers_status(project)["workers"]}
        for row in overview(project, draft_result)["changes"]:
            worker = workers.get(row["change_id"])
            if worker and (row["waiting"] == "human" or worker["flags"]):
                # Roadmap H: a worker waits for a person, or the watchdog flagged a running one.
                row = {**row, "status": worker["run_status"], "next": worker["next"],
                       "flags": worker["flags"], "worker": worker["worker"]}
            elif row["waiting"] != "human":
                continue
            items.append({**row, "project": str(project)})
        from .github_events import pending_events
        for event in pending_events(project):  # roadmap H: GitHub events waiting for an assessment
            items.append({"project": str(project), "change_id": event["id"],
                          "status": "EVENT_CI_RED" if event["kind"] == "ci_red" else "EVENT_ISSUE",
                          "class": None, "goal": event["title"], "updated_at": event["at"],
                          "next": event["next"] + f"；不处理：storyloop github dismiss {project} --event {event['id']}"})
    items.sort(key=lambda item: item.get("updated_at") or "")
    return {"status": "INBOX", "projects": [str(Path(item).resolve()) for item in projects], "items": items}


def render_inbox(result: dict, now: datetime | None = None) -> str:
    items = result["items"]
    if not items:
        return "INBOX  没有待你决定的事项"
    projects = len({item["project"] for item in items})
    lines = [f"INBOX  {len(items)} 个待你决定" + (f"（{projects} 个项目）" if projects > 1 else "")]
    from .intervention_events import CLASS_LABELS
    for number, item in enumerate(items, 1):
        label = CLASS_LABELS.get(item.get("class") or "", "")
        waited = _age(item.get("updated_at"), now or datetime.now(timezone.utc))
        flagged = f" ⚠ {'、'.join(item['flags'])}" if item.get("flags") else ""
        lines += ["", f"[{number}] {Path(item['project']).name}  {item['change_id']}  {item['status']}"
                      + (f"（{label}）" if label else "") + (f" · 已等待 {waited}" if waited else "") + flagged,
                  f"    {item['goal'][:80]}"]
        if item.get("next"):
            lines.append(f"    下一步：{item['next']}")
    return "\n".join(lines)


def _age(stamp: str | None, now: datetime) -> str | None:
    try:
        then = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    minutes = max(0, int((now - then).total_seconds() // 60))
    return f"{minutes} 分钟" if minutes < 120 else f"{minutes // 60} 小时" if minutes < 2880 else f"{minutes // 1440} 天"


NOTIFY_COMMANDS = frozenset({"approve", "run", "change", "answer", "accept", "reset", "acceptance-repair"})


def notify(args, result: dict) -> None:
    """Run the project's `notify_command` once a command ends where a person is needed (or done).

    Opt-in per project; the command gets one JSON object on stdin and never blocks or fails the
    StoryLoop command. Runs still moving and read-only commands never notify.
    """
    project = getattr(args, "project", None)
    if getattr(args, "command", None) not in NOTIFY_COMMANDS or not isinstance(project, Path):
        return
    from .run_state import ACTIVE_STATUSES
    status = result.get("status")
    if not status or status in ACTIVE_STATUSES:
        return
    try:
        from .baseline import load_project_config
        command = load_project_config(project.resolve()).get("notify_command")
    except (OSError, ValueError, StoryLoopError):
        return
    if not (isinstance(command, list) and command and all(isinstance(item, str) for item in command)):
        return
    event = {"event": "passed" if status == "PASSED" else "decision", "project": str(project.resolve()),
             "change_id": result.get("change_id") or getattr(args, "change_id", None),
             "status": status, "next": result.get("next")}
    try:
        subprocess.run(command, input=json.dumps(event, ensure_ascii=False), text=True, encoding="utf-8",
                       capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return
