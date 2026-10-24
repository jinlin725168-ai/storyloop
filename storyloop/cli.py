"""Public command-line interface, with honest machine-readable exit codes."""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import sys
from . import __version__
from .baseline import BASELINE_PATH
from .onboarding_cli import initialize, add_init_arguments
from .base_upgrade import propose_base_change
from .foundation_survey import survey_foundation
from .skills import install_skills
from .skill_custom import write_view
from .system_smoke import apply_smoke_update, propose_smoke_update
from .next_steps import next_step
from .models import StoryLoopError, require
from .processutil import redact
from .project_agents import invoke_project_agent
from .project_loop import load_run_state, run_change
from .stop_report import build_stop, build_entry_stop, stop_of
from .routing_cli import dispatch_routing, BLOCKED
from .ci_command import run_ci_command
from .memory_cli import add_memory_arguments, dispatch_memory
from .cli_output import display


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="storyloop", description="产品需求与外部验收驱动的工程迭代闭环")
    root.add_argument("--version", action="version", version=__version__)
    sub = root.add_subparsers(dest="command", required=True)
    descriptions = {
        "readiness": "检查项目测试准备度",
        "init": "一键锁定现有 Git 工程底座",
        "change": "用一句产品意图与 AI 共创 OpenSpec 用户故事和验收条件",
        "approve": "批准产品合同，并自动完成实现、验收、报告和本地合并",
        "run": "执行或继续已批准的产品变更",
        "status": "查看产品变更状态与下一步命令",
        "ci": "CI 入口：隔离运行该变更的验收套件和项目级系统冒烟",
        "report": "定位某次变更的验收报告",
        "base-propose": "为计划中的底座改动开一个 BASE 提案（无需先触发功能变更）",
        "base-update": "兼容旧 BASE 提案：待批准提案导入变更草案，历史回执继续恢复",
        "base-review": "由独立的人对已提交的底座候选记录具名审查",
        "answer": "回答代理在 NEEDS_INPUT 停点提出的问题（--answer 可重复，按问题顺序）",
        "accept": "记录人工验收：--change 与 --criterion（仅在 MANUAL_ACCEPTANCE_REQUIRED 停点可用）",
        "log": "查看一个变更的全过程：每次代理调用（结果、耗时、费用、会话、调用记录目录）、每轮失败原因和下一步",
        "reset": "人工复位卡住的运行（WORKSPACE_TAINTED 或工作区已变的人工验收等待），需说明理由",
        "acceptance-repair": "变更验收用例本身有缺陷时，让 acceptance 角色修复自己的套件并从合同提交重新证明 RED，需说明理由",
        "smoke-repair": "acceptance-repair 的旧名",
        "document": "为已完成的变更（重新）生成 OpenSpec 文档：增量规格、decisions.md、活规格；--design 附加方案文件",
        "smoke-propose": "变更合并后，让 acceptance 角色提议把哪些核心链路并入 / 更新项目级系统冒烟，草案写到 .storyloop/drafts/smoke/<id>/，由人审核",
        "smoke-apply": "把审核过的系统冒烟草案安装到 acceptance/smoke/，隔离跑绿后提交；红的草案不会留下任何改动",
        "skills": "把引擎自带的技能（foundation-survey 等）安装或刷新到项目的 .claude/skills 与 .codex/skills；已初始化的项目会自动开 BASE 提案",
        "survey": "底座摸底：生成 openspec/foundation/ 候选与 BASE 草案；批准后走人工 base 通道",
        "lessons": "列出、确认或退休仓库经验", "lessons-propose": "复盘已结束的变更并提议经验草案",
        "drafts": "列出或驳回待批草案",
        "metrics": "跨变更汇总指标与调整建议（只读）",
    }
    for command, help_text in descriptions.items():
        child = sub.add_parser(command, help=help_text)
        child.add_argument("project", type=Path)
        child.add_argument("--json", action="store_true", help="输出完整 JSON")
        _product_arguments(child, command)
        _maintenance_arguments(child, command)
        add_memory_arguments(child, command)
    adapters = sub.add_parser("adapters", help="检查角色适配器能力")
    adapter_sub = adapters.add_subparsers(dest="adapter_command", required=True)
    check = adapter_sub.add_parser("check", help="握手并校验角色档次映射")
    check.add_argument("project", type=Path)
    check.add_argument("--json", action="store_true", help="输出完整 JSON")
    return root


def _product_arguments(child, command):
    if command == "readiness":
        child.add_argument("--change", dest="change_id")
    elif command == "init":
        add_init_arguments(child)
    elif command == "change":
        child.add_argument("--idea")
        child.add_argument("--slug", help="新变更 ID 中的功能短名（3–40 个小写字母、数字、连字符）")
        child.add_argument("--change", dest="change_id")
        child.add_argument("--message")
        child.add_argument("--supersede",
                           help="作废旧运行并重开草案；切回源分支后用 --change 新ID --message 意见"
                                "重新评估，再明确批准")
        child.add_argument("--reason",
                           help="在 NEEDS_SPEC_FIX 以外的停点作废时，说明为什么是合同的问题")
        child.add_argument("--no-interactive", action="store_true",
                           help="有待决问题时不在终端逐条澄清")
    elif command == "log":
        child.add_argument("--change", dest="change_id", required=True)
    elif command in {"approve", "status", "report", "ci"}:
        group = child.add_mutually_exclusive_group() if command == "approve" else child
        group.add_argument("--change", dest="change_id", required=command == "ci")
        if command == "ci":
            child.add_argument("--record", type=Path)
        if command == "approve":
            group.add_argument("--draft", help="审批经验或勘误草案")
            for flag in ("readiness", "design", "continue-heavy", "option", "reason", "tier"):
                child.add_argument("--" + flag)
            child.add_argument("--no-interactive", action="store_true",
                               help="停在 NEEDS_INPUT 时不在终端提问")
    elif command == "answer":
        child.add_argument("--change", dest="change_id", required=True)
        child.add_argument("--answer", dest="answers", action="append", default=[], required=True)
    elif command == "base-update":
        child.add_argument("--proposal", required=True)
        child.add_argument("--confirm-oracle-skills", action="store_true")
    elif command == "skills":
        child.add_argument("--view", metavar="NAME",
                           help="生成技能的项目定制视图，写到 .storyloop/skills-view/")
    elif command == "base-review":
        child.add_argument("--change", dest="change_id", required=True)
        child.add_argument("--verdict", choices=("pass", "fail"), required=True)
        child.add_argument("--note", required=True)


def _maintenance_arguments(child, command):
    if command == "metrics":
        child.add_argument("--since", type=_since_date, metavar="YYYY-MM-DD")
        child.add_argument("--ci-results", type=Path, metavar="DIR")
        child.add_argument("--save", action="store_true")
    elif command == "document":
        child.add_argument("--change", dest="change_id", required=True)
        child.add_argument("--design", type=Path)
        child.add_argument("--capability", help="合同未命名能力时指定活规格名称")
    elif command in {"smoke-propose", "smoke-apply"}:
        child.add_argument("--change", dest="change_id", required=True)
    elif command == "survey":
        child.add_argument("--reason", default="foundation survey")
        child.add_argument("--from", dest="from_file", type=Path,
                           help="不再调用代理，从保存的摸底结果安装（校验失败后人工修正再装）")
    elif command in {"reset", "smoke-repair", "acceptance-repair"}:
        child.add_argument("--change", dest="change_id", required=True)
        child.add_argument("--reason", required=True)
    elif command == "base-propose":
        child.add_argument("--path", dest="paths", action="append", default=[], required=True)
        child.add_argument("--reason", required=True)
        child.add_argument("--allow-mutable", action="store_true",
                           help="上游同步：允许提案包含可变区路径")
        child.add_argument("--allow-ci", action="store_true",
                           help="由人新增或修改 CI 配置（.github/workflows 等）；代理永远不能")
    elif command == "run":
        child.add_argument("--replan", action="store_true")
        child.add_argument("--reason")
        child.add_argument("--change", dest="change_id", required=True)
        child.add_argument("--implementer", choices=("human",),
                           help="人工提交 base 变更；代理通道留待 E2")
        child.add_argument("--confirm-oracle-skills", action="store_true",
                           help="明确确认本次 base 候选对生效 Oracle 技能的修改")
        child.add_argument("--overrule", help="不同意审查对 ORACLE_DEFECT / NEEDS_SPEC_FIX 的判定：写明理由")
        child.add_argument("--no-interactive", action="store_true",
                           help="停在 NEEDS_INPUT 时不在终端提问")
    elif command == "accept":
        child.add_argument("--change", dest="change_id", required=True)
        child.add_argument("--criterion", dest="criteria", action="append", default=[], required=True)
        child.add_argument("--note", required=True)


def _since_date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("日期必须是 YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise argparse.ArgumentTypeError("日期必须是 YYYY-MM-DD")
    return parsed


def dispatch(args) -> dict:
    project = args.project.resolve()
    if args.command == "adapters":
        from .adapter_capabilities import check
        return check(project)
    if args.command == "approve" and getattr(args, "draft", None):
        return dispatch_memory(project, args)
    routed = dispatch_routing(project, args)
    if routed is not None:
        return routed
    if args.command == "init":
        return initialize(project, args)
    if args.command == "change":
        return _change(project, args)
    if args.command == "base-propose":
        require(_is_product_project(project), "Run storyloop init on this Git project first")
        return propose_base_change(project, args.paths, args.reason, allow_mutable=args.allow_mutable,
                                   allow_ci=args.allow_ci)
    if args.command == "base-update":
        require(_is_product_project(project), "Run storyloop init on this Git project first")
        from .oracle_skill_approval import confirmed_upgrade
        return confirmed_upgrade(project, args.proposal, args.confirm_oracle_skills)
    if args.command == "base-review":
        from .base_execution import record_base_review
        return record_base_review(project, args.change_id, args.verdict, args.note)
    if args.command == "skills":
        if args.view is not None:
            require(bool(args.view), "Skill view name must not be empty")
            return write_view(project, args.view)
        return install_skills(project)
    if args.command == "smoke-propose":
        require(_is_product_project(project), "Run storyloop init on this Git project first")
        return propose_smoke_update(project, args.change_id)
    if args.command == "smoke-apply":
        require(_is_product_project(project), "Run storyloop init on this Git project first")
        return apply_smoke_update(project, args.change_id)
    if args.command == "survey":
        require(_is_product_project(project), "Run storyloop init on this Git project first")
        return survey_foundation(project, args.reason, from_file=args.from_file)
    if args.command in {"lessons", "drafts", "lessons-propose"}:
        require(_is_product_project(project), "Run storyloop init on this Git project first")
        return dispatch_memory(project, args)
    if args.command == "ci":
        return run_ci_command(project, args.change_id, args.record)
    if args.command == "metrics":
        from .metrics import build_metrics, save_metrics
        result = build_metrics(project, args.since, args.ci_results)
        if args.save:
            result["written"] = save_metrics(project, result)
        return result
    require(_is_product_project(project), "Run storyloop init on this Git project first")
    if args.command in {"approve", "run"} and getattr(args, "change_id", None):
        from .changes import load_change
        from .change_types import type_of
        if type_of(load_change(project, args.change_id)["product"]) == "base":
            if args.command == "approve":
                from .approvals import approve_change
                approved = approve_change(project, args.change_id)
                return {"status": "APPROVED", "change_id": args.change_id,
                        "approval": approved["approval"],
                        "next": f"storyloop run <project> --change {args.change_id} --implementer human"}
            from .oracle_skill_approval import CONFIRMED
            token = CONFIRMED.set(args.confirm_oracle_skills)
            try:
                return run_change(project, args.change_id, implementer=args.implementer)
            finally:
                CONFIRMED.reset(token)
    return _product_dispatch(project, args)


def _change(project: Path, args) -> dict:
    from .product_cli import change
    return change(project, args, _product_answer, _revise_with_agent,
                  clarify_interactively, _draft_result, _is_product_project, invoke_project_agent)


def _draft_result(project, state, next_action=None):
    from .product_cli import draft_result
    return draft_result(project, state, next_action)


def _revise_with_agent(project: Path, change_id: str, message: str, answers: list[dict]) -> dict:
    from .product_cli import revise_with_agent
    return revise_with_agent(project, change_id, message, answers, _product_answer)


def _product_answer(project: Path, payload: dict, call=None) -> dict:
    from .product_cli import product_answer
    return product_answer(project, payload, call, invoke_project_agent)


def clarify_interactively(project: Path, change_id: str, reviser, interactive: bool,
                          per_round: int = 5) -> dict:
    from .product_cli import clarify_interactively as clarify
    return clarify(project, change_id, reviser, interactive, per_round,
                   _terminal, _say, _pick_answer)


def _say(text: str) -> None:
    """Print prompts without dying on consoles that cannot encode them (Windows cp1252)."""
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        print(text.encode(encoding, "replace").decode(encoding, "replace"))


def _pick_answer(reply: str, question: dict) -> str | None:
    if not reply:
        return question.get("recommended")
    if reply.isdigit() and 1 <= int(reply) <= len(question["options"]):
        return question["options"][int(reply) - 1]
    return reply


def _product_dispatch(project: Path, args) -> dict:
    from .product_cli import dispatch
    return dispatch(project, args, run_with_answers, _draft_result, load_run_state)


def run_with_answers(project: Path, change_id: str, interactive: bool,
                     runner=None, overrule: str | None = None) -> dict:
    from .product_cli import run_with_answers as run_interactively
    return run_interactively(project, change_id, interactive, runner or run_change,
                             overrule, _terminal, _say)


def _terminal() -> bool:
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


def _is_product_project(project: Path) -> bool:
    return (project / BASELINE_PATH).is_file()


def _error_stop(args, exc: StoryLoopError) -> dict | None:
    """Best-effort stop context; a damaged run must not mask the original error."""
    change_id = getattr(args, "change_id", None)
    if exc.code in BLOCKED or exc.code == "NEEDS_INPUT":
        if not change_id or not (args.project.resolve() / ".storyloop/state/runs" / f"{change_id}.json").exists():
            return build_entry_stop(args.project.resolve(), exc.code,
                                    {**(exc.details or {}), **({"change_id": change_id} if change_id else {})})
    if not change_id or getattr(args, "command", None) not in {"run", "approve"}:
        return None
    project = args.project.resolve()
    path = project / ".storyloop/state/runs" / f"{change_id}.json"
    if not path.exists():
        return None
    try:
        run = load_run_state(project, change_id)
        return stop_of(project, run) or build_stop(
            project, run, exc.code, {"error": redact(str(exc))})
    except (OSError, ValueError, TypeError, KeyError, StoryLoopError):
        return None


def _redact_value(value: object) -> object:
    if isinstance(value, dict):
        return {key: _redact_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    return redact(value) if isinstance(value, str) else value


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    args = parser().parse_args(argv)
    try:
        result = dispatch(args)
        display(result, args.json)
        failures = {
            "FAILED", "BLOCKED", "NEEDS_INPUT", "INTERRUPTED",
            "UNSAFE_CONTAINMENT", "WORKSPACE_TAINTED",
            "BUDGET_EXHAUSTED", "BASE_CHANGE_REQUIRED",
            "BOUNDARY_VIOLATION", "ERROR", "CHARACTERIZATION_FAILED",
            "SCAFFOLD_SELECTION_REQUIRED", "SMOKE_UPDATE_FAILED", "EXISTING_GREEN", "ORACLE_DEFECT", "NEEDS_SPEC_FIX", "NO_PROGRESS",
        }
        return 2 if result.get("status") in failures | BLOCKED else 0
    except StoryLoopError as exc:
        error = {"status": exc.code, "message": redact(str(exc))}
        change_id = getattr(args, "change_id", None)
        stop = _error_stop(args, exc)
        if stop is not None:
            error["stop"] = stop
        hint = stop["resume"] if stop else next_step(exc.code, change_id, args.project.resolve(), exc.details)
        if hint:
            error["next"] = hint
        print(json.dumps(_redact_value(error), ensure_ascii=False), file=sys.stderr)
        return 2
    except (OSError, ValueError, TypeError) as exc:
        print(json.dumps({"status": "BLOCKED", "message": "Operation could not complete: " + type(exc).__name__}), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted; state saved. Resume with run.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
