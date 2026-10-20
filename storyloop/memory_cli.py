"""CLI wiring and presentation for repository lessons."""

from __future__ import annotations

from .lesson_store import confirm_lesson, list_lessons, retire_lesson
from .models import require


def add_lessons_arguments(child) -> None:
    actions = child.add_mutually_exclusive_group()
    actions.add_argument("--confirm", metavar="ID", help="确认经验仍然有效")
    actions.add_argument("--retire", metavar="ID", help="退休经验")
    child.add_argument("--reason", help="退休理由（与 --retire 一起使用）")


def dispatch_lessons(project, args):
    if args.confirm:
        require(not args.reason, "--reason 只与 --retire 一起使用")
        return confirm_lesson(project, args.confirm)
    if args.retire:
        require(args.reason, "--retire 必须提供 --reason")
        return retire_lesson(project, args.retire, args.reason)
    require(not args.reason, "--reason 只与 --retire 一起使用")
    return list_lessons(project)


def render_lessons(result: dict) -> str:
    headings = ("ID", "摘要", "层", "强度", "状态", "复发次数")
    rows = [headings]
    statuses = {"active": "有效", "needs-review": "需复查", "retired": "已退休"}
    for item in result["lessons"]:
        summary = item["summary"] + ("（建议升级为检查）" if item["recurrences"] >= 2 else "")
        rows.append((item["id"], summary, item["tier"], item["strength"],
                     statuses[item["effective_status"]], str(item["recurrences"])))
    widths = [max(len(row[index]) for row in rows) for index in range(len(headings))]
    lines = ["  ".join(value.ljust(widths[index]) for index, value in enumerate(row)) for row in rows]
    signal = result["vector_signal"]
    if signal["reevaluate"]:
        lines.append("经验已超过 200 条或提供后复发率超过 30%，建议重新评估是否需要检索")
    return "\n".join(lines)
