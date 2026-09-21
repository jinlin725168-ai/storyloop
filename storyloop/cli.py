"""Public command-line interface, with honest machine-readable exit codes."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from . import __version__, discovery, engine, v2_cli
from .answers import pending_questions, record_answers
from .approvals import approve_change
from .baseline import BASELINE_PATH, bootstrap_project
from .base_upgrade import propose_base_change, upgrade_baseline
from .changes import create_change, load_change, revise_change
from .manual_acceptance import record_manual_acceptance
from .openspec_docs import document_change
from .models import StoryLoopError, require
from .processutil import redact
from .project_agents import invoke_project_agent
from .project_loop import load_run_state, run_change
from .report import render
from .run_recovery import reset_run
from .smoke_runner import run_smoke_isolated
from .storage import initialize, load_json
from .telemetry import adopt_pending, summarize


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="storyloop", description="产品需求与外部验收驱动的工程迭代闭环")
    root.add_argument("--version", action="version", version=__version__)
    sub = root.add_subparsers(dest="command", required=True)
    descriptions = {
        "init": "一键锁定现有 Git 工程底座，或创建旧版独立实例",
        "change": "用一句产品意图与 AI 共创 OpenSpec 用户故事和验收条件",
        "approve": "批准产品合同，并自动完成实现、验收、报告和本地合并",
        "run": "执行或继续已批准的产品变更（旧实例也可按故事运行）",
        "status": "查看产品变更或旧实例状态",
        "ci": "独立运行某次变更的核心流程冒烟测试",
        "report": "定位某次变更的验收报告或生成旧实例报告",
        "base-propose": "为计划中的底座改动开一个 BASE 提案（无需先触发功能变更）",
        "base-update": "按 BASE 提案校验独立底座提交并升级底座版本",
        "discuss": "旧实例：与 AI 共创故事和验收边界",
        "prepare": "旧实例：生成验收测试与初始骨架（不执行）",
        "answer": "回答代理在 NEEDS_INPUT 停点提出的问题（--answer 可重复，按问题顺序）",
        "accept": "记录人工验收：产品变更用 --change 与 --criterion，旧实例用 --story",
        "reset": "人工复位卡住的运行（WORKSPACE_TAINTED 或工作区已变的人工验收等待），需说明理由",
        "document": "为已完成的变更（重新）生成 OpenSpec 文档：增量规格、decisions.md、活规格；--design 附加方案文件",
        "demo": "运行自带脚本示例（不调用 AI）",
    }
    for command, help_text in descriptions.items():
        child = sub.add_parser(command, help=help_text)
        child.add_argument("project", type=Path)
        child.add_argument("--json", action="store_true", help="输出完整 JSON")
        if command == "init":
            child.add_argument("--goal", default="")
            child.add_argument("--settings", type=Path,
                               help="可选 JSON：显式 test_commands/module_roots/integration_paths")
        elif command == "change":
            child.add_argument("--idea")
            child.add_argument("--change", dest="change_id")
            child.add_argument("--message")
            child.add_argument("--no-interactive", action="store_true",
                               help="有待决问题时不在终端逐条澄清")
        elif command in {"approve", "status", "report", "ci"}:
            child.add_argument("--change", dest="change_id", required=command == "ci")
            if command == "approve":
                child.add_argument("--no-interactive", action="store_true",
                                   help="停在 NEEDS_INPUT 时不在终端提问")
        elif command == "answer":
            child.add_argument("--change", dest="change_id", required=True)
            child.add_argument("--answer", dest="answers", action="append", default=[], required=True)
        elif command == "base-update":
            child.add_argument("--proposal", required=True)
        elif command == "document":
            child.add_argument("--change", dest="change_id", required=True)
            child.add_argument("--design", type=Path)
        elif command == "reset":
            child.add_argument("--change", dest="change_id", required=True)
            child.add_argument("--reason", required=True)
        elif command == "base-propose":
            child.add_argument("--path", dest="paths", action="append", default=[], required=True)
            child.add_argument("--reason", required=True)
            child.add_argument("--allow-mutable", action="store_true",
                               help="上游同步：允许提案包含可变区路径")
        elif command == "discuss":
            child.add_argument("--message", required=True)
        elif command == "run":
            child.add_argument("--change", dest="change_id")
            child.add_argument("--story")
            child.add_argument("--no-interactive", action="store_true",
                               help="停在 NEEDS_INPUT 时不在终端提问")
        elif command == "accept":
            child.add_argument("--story")
            child.add_argument("--change", dest="change_id")
            child.add_argument("--criterion", dest="criteria", action="append", default=[])
            child.add_argument("--note", required=True)
    v2_cli.add_parsers(sub)
    return root


def dispatch(args) -> dict:
    project = args.project.resolve()
    if args.command in v2_cli.STAGE_COMMANDS:
        return v2_cli.dispatch(args)
    if args.command == "init":
        if (project / ".git").exists():
            settings = load_json(args.settings.resolve()) if args.settings else None
            return bootstrap_project(project, settings)
        initialize(project, args.goal)
        return {"status": "DRAFT", "project": str(project), "next": "discuss --message 你的想法"}
    if args.command == "change":
        return _change(project, args)
    if args.command == "base-propose":
        require(_is_product_project(project), "Run storyloop init on this Git project first")
        return propose_base_change(project, args.paths, args.reason, allow_mutable=args.allow_mutable)
    if args.command == "base-update":
        require(_is_product_project(project), "Run storyloop init on this Git project first")
        return upgrade_baseline(project, args.proposal)
    if args.command == "ci":
        return run_smoke_isolated(project, args.change_id)
    if _is_product_project(project):
        return _product_dispatch(project, args)
    return _legacy_dispatch(project, args)


def _change(project: Path, args) -> dict:
    require(_is_product_project(project), "Run storyloop init on this Git project first")
    interactive = not getattr(args, "no_interactive", False)
    if args.idea:
        require(not args.change_id and not args.message,
                "Use --idea for a new change, or --change with --message for a revision")
        product = invoke_project_agent(project, "product", {"idea": args.idea})
        created = create_change(project, args.idea, product)
        adopt_pending(project, created["change_id"])
        return clarify_interactively(project, created["change_id"], _revise_with_agent, interactive)
    require(args.change_id and args.message,
            "Revision needs both --change and --message")
    revised = _revise_with_agent(project, args.change_id, args.message, [])
    return clarify_interactively(project, args.change_id, _revise_with_agent, interactive)


def _revise_with_agent(project: Path, change_id: str, message: str, answers: list[dict]) -> dict:
    """One product revision: the agent rewrites the contract, the engine records the answers."""
    current = load_change(project, change_id)
    product = invoke_project_agent(project, "product", {
        "change_id": change_id, "message": message,
        "answers": [{"question_id": a["question_id"], "question": a["question"], "answer": a["answer"]}
                    for a in answers],
        "current_product": current["product"],
    })
    return revise_change(project, change_id, product, message,
                         clarifications=[{"question": a["question"], "answer": a["answer"]} for a in answers])


def clarify_interactively(project: Path, change_id: str, reviser, interactive: bool,
                          per_round: int = 5) -> dict:
    """Spec-kit style clarification: up to five multiple-choice questions per round, on the record.

    Empty input takes the recommended option; a number picks an option; free text is accepted;
    `q` stops the round and leaves the remaining questions open. Without a terminal the draft is
    returned untouched so the questions can be answered later with `change --message`.
    """
    state = load_change(project, change_id)
    while state["product"]["questions"] and interactive and _terminal():
        questions = state["product"]["questions"][:per_round]
        print(f"草案有 {len(state['product']['questions'])} 个待决问题，本轮澄清 {len(questions)} 个"
              "（回车选推荐项，输入序号或自由文本，q 结束本轮）：")
        answers = []
        stopped = False
        for index, question in enumerate(questions, 1):
            print(f"\n[{question['id']}] {question['text']}")
            for number, option in enumerate(question["options"], 1):
                marker = "（推荐）" if option == question.get("recommended") else ""
                print(f"  {number}. {option}{marker}")
            if question.get("why"):
                print(f"  为什么重要：{question['why']}")
            reply = input("> ").strip()
            if reply.lower() == "q":
                stopped = True
                break
            answer = _pick_answer(reply, question)
            if answer is None:
                continue
            answers.append({"question_id": question["id"], "question": question["text"], "answer": answer})
        if not answers:
            break
        message = "澄清回答（请据此更新故事、规则和验收条件，删除已回答的问题，并保留 clarifications）：\n" + \
            "\n".join(f"{a['question_id']} {a['question']} → {a['answer']}" for a in answers)
        state = reviser(project, change_id, message, answers)
        if stopped:
            break
    return state


def _pick_answer(reply: str, question: dict) -> str | None:
    if not reply:
        return question.get("recommended")
    if reply.isdigit() and 1 <= int(reply) <= len(question["options"]):
        return question["options"][int(reply) - 1]
    return reply


def _product_dispatch(project: Path, args) -> dict:
    change_id = getattr(args, "change_id", None)
    if args.command == "approve":
        require(change_id, "Product approval needs --change")
        approve_change(project, change_id)
        return run_with_answers(project, change_id, interactive=not args.no_interactive)
    if args.command == "run":
        require(change_id, "Product execution needs --change")
        return run_with_answers(project, change_id, interactive=not args.no_interactive)
    if args.command == "answer":
        return record_answers(project, change_id, args.answers)
    if args.command == "status":
        require(change_id, "Product status needs --change")
        change = load_change(project, change_id)
        run_path = project / ".storyloop/state/runs" / f"{change_id}.json"
        return {"status": change["status"], "change_id": change_id,
                "revision": change["revision"],
                "run": load_run_state(project, change_id) if run_path.exists() else None,
                "telemetry": summarize(project, change_id)}
    if args.command == "reset":
        return reset_run(project, change_id, args.reason)
    if args.command == "document":
        return document_change(project, change_id, args.design.resolve() if args.design else None)
    if args.command == "accept":
        require(change_id and args.criteria, "Manual acceptance needs --change and --criterion")
        return record_manual_acceptance(project, change_id, args.criteria, args.note)
    if args.command == "report":
        require(change_id, "Product report needs --change")
        path = project / "reports" / "storyloop" / change_id / "report.md"
        require(path.is_file(), "Acceptance report is not available")
        return {"status": "REPORT", "change_id": change_id, "report": str(path)}
    raise StoryLoopError(f"{args.command} is only available for a legacy StoryLoop instance")


def _legacy_dispatch(project: Path, args) -> dict:
    if args.command == "discuss":
        return discovery.discuss(project, args.message)
    if args.command == "prepare":
        return discovery.prepare(project)
    if args.command == "approve":
        return {"status": "APPROVED", **engine.approve(project)}
    if args.command == "accept":
        require(args.story, "Legacy acceptance needs --story")
        return engine.accept(project, args.story, args.note)
    if args.command == "report":
        return {"report": str(render(project))}
    if args.command == "demo":
        from .demo import create_demo
        create_demo(project)
        engine.approve(project)
        result = engine.run(project)
        result["report"] = str(render(project))
        result["demo"] = "Scripted fixture; external checks and code changes are real. No AI model was called."
        return result
    return engine.run(project, args.story) if args.command == "run" else engine.status(project)


def run_with_answers(project: Path, change_id: str, interactive: bool,
                     runner=None) -> dict:
    """Run the change; when it pauses for input and a terminal is present, ask right here.

    Answers are recorded exactly as `storyloop answer` would record them, then the run
    continues. Without a terminal (CI, pipes) the NEEDS_INPUT result is returned unchanged.
    """
    runner = runner or run_change  # resolved at call time so tests can patch cli.run_change
    result = runner(project, change_id)
    while result.get("status") == "NEEDS_INPUT" and interactive and _terminal():
        questions = pending_questions(project, change_id)
        if not questions:
            break
        print(f"代理有 {len(questions)} 个问题需要你决定（直接回车表示让代理自行决定）：")
        answers = []
        for index, question in enumerate(questions, 1):
            reply = input(f"[{index}/{len(questions)}] {question}\n> ").strip()
            answers.append(reply or "由人确认：此问题无特别要求，由代理按现有约定自行决定")
        record_answers(project, change_id, answers)
        result = runner(project, change_id)
    return result


def _terminal() -> bool:
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


def _is_product_project(project: Path) -> bool:
    return (project / BASELINE_PATH).is_file()


def display(result: dict, complete_json: bool) -> None:
    if complete_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if "goal" in result:
        print(result["goal"])
        for story in result.get("stories", []):
            print(f"  {story['id']}  {story['title']} ({len(story['acceptance'])} 项验收)")
        for question in result.get("questions", []):
            print(f"  ? {question['id']}: {question['text']}")
        return
    print(result.get("status", "REPORT"))
    for key in ("change_id", "project", "next", "report", "demo", "pending", "accepted", "paths", "written"):
        if key in result and result[key] is not None:
            print(f"{key}: {result[key]}")
    for warning in result.get("warnings", []):
        print(f"warning: {warning}")


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    args = parser().parse_args(argv)
    try:
        result = dispatch(args)
        display(result, args.json)
        failures = {
            "FAILED", "BLOCKED", "NEEDS_INPUT", "STALE", "INTERRUPTED",
            "ARCHITECTURE_DECISION_REQUIRED", "UNSAFE_CONTAINMENT", "WORKSPACE_TAINTED",
            "BUDGET_EXHAUSTED", "REPEATED_FAILURE", "BASE_CHANGE_REQUIRED",
            "BOUNDARY_VIOLATION", "ERROR",
        }
        return 2 if result.get("status") in failures else 0
    except StoryLoopError as exc:
        print(json.dumps({"status": exc.code, "message": redact(str(exc))}, ensure_ascii=False), file=sys.stderr)
        return 2
    except (OSError, ValueError, TypeError) as exc:
        print(json.dumps({"status": "BLOCKED", "message": "Operation could not complete: " + type(exc).__name__}), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted; state saved. Resume with run.", file=sys.stderr)
        return 130
