"""Cross-change overview (roadmap F3): `storyloop status <project>` without --change.

One read-only answer to "what needs me, what is moving, what is done", with the next command
for every change that waits on a person.
"""
from __future__ import annotations

from pathlib import Path

from .intervention_events import CLASS_LABELS, stop_class
from .run_state import ACTIVE_STATUSES

DONE = frozenset({"PASSED", "SUPERSEDED"})
DONE_SHOWN = 5


def overview(project: Path, draft_result) -> dict:
    project = Path(project).resolve()
    rows = [_row(project, path.stem, draft_result) for path in sorted((project / ".storyloop/state/changes").glob("*.json"))]
    rows.sort(key=lambda row: row["updated_at"] or "", reverse=True)
    groups = {name: [row for row in rows if row["waiting"] == name] for name in ("human", "running", "done")}
    return {"status": "OVERVIEW", "project": str(project), "changes": rows,
            "counts": {name: len(items) for name, items in groups.items()}}


def change_next(project: Path, change: dict, run: dict | None, draft_result) -> str | None:
    """The same next step `status --change` prints, shared so the two never disagree."""
    from .next_steps import append_pending_memory, next_step
    if run is None and change["status"] == "DRAFT":
        hint = draft_result(project, change)["next"]
    else:
        hint = (next_step((run or {}).get("status") or change["status"], change["change_id"], project, run)
                or ((run or {}).get("stop") or {}).get("resume"))
    return append_pending_memory(project, hint)


def _row(project: Path, change_id: str, draft_result) -> dict:
    from .changes import load_change
    from .project_loop import load_run_state
    change = load_change(project, change_id)
    run_file = project / ".storyloop/state/runs" / f"{change_id}.json"
    run = load_run_state(project, change_id) if run_file.exists() else None
    status = (run or {}).get("status") or change["status"]
    waiting = "done" if status in DONE else "running" if status in ACTIVE_STATUSES else "human"
    from .workers import worker_note
    note = worker_note(project, change_id)
    if note is not None:  # roadmap H: the change runs (or stopped) in its own worker clone
        waiting = "running" if note["alive"] else "human"
        return {"change_id": change_id, "status": "IN_WORKER", "waiting": waiting, "class": None,
                "goal": (change.get("product") or {}).get("goal") or change.get("idea") or "",
                "updated_at": change.get("updated_at"), "worker": note["worker"],
                "next": f"storyloop workers status {project}"}
    return {"change_id": change_id, "status": status, "waiting": waiting,
            "class": stop_class(status) if waiting == "human" else None,
            "goal": (change.get("product") or {}).get("goal") or change.get("idea") or "",
            "updated_at": (run or {}).get("updated_at") or change.get("updated_at"),
            "next": change_next(project, change, run, draft_result) if waiting != "done" else None}


def render_overview(result: dict) -> str:
    counts = result["counts"]
    lines = [f"OVERVIEW  需要你决定 {counts['human']} · 进行中 {counts['running']} · 已完成 {counts['done']}"]
    sections = (("human", "需要你决定", None), ("running", "进行中（没有进程在跑时用下一步命令继续）", None),
                ("done", "已完成（最近）", DONE_SHOWN))
    for key, title, limit in sections:
        rows = [row for row in result["changes"] if row["waiting"] == key][:limit]
        if not rows:
            continue
        lines += ["", f"{title}："]
        for row in rows:
            label = f"（{CLASS_LABELS.get(row['class'], '')}）" if row.get("class") else ""
            lines.append(f"  {row['change_id']}  {row['status']}{label}  {_short(row['goal'])}")
            if row.get("next"):
                lines.append(f"    下一步：{row['next']}")
    if not result["changes"]:
        lines += ["", "还没有变更：storyloop change <项目> --idea \"<一句话需求>\""]
    return "\n".join(lines)


def _short(text: str, limit: int = 60) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit - 1] + "…"
