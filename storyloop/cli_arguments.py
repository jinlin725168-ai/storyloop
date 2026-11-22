"""Per-command CLI argument registration, split from cli.py (entry-file size limit)."""
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from .onboarding_cli import add_init_arguments


def _product_arguments(child, command):
    if command == "readiness":
        child.add_argument("--change", dest="change_id")
    elif command == "init":
        add_init_arguments(child)
    elif command == "assess":
        child.add_argument("--idea", required=True)
        child.add_argument("--problem", help="谁遇到了什么问题（讲一次真实发生的情况）")
        child.add_argument("--success", help="凭什么知道问题解决了")
        child.add_argument("--if-not", dest="if_not", help="不做会怎样")
        child.add_argument("--decision", choices=("do", "clarify", "drop"))
        child.add_argument("--no-interactive", action="store_true", help="缺项时报错，不在终端提问")
    elif command == "change":
        child.add_argument("--assessment", help="storyloop assess 结论为“做”的评估记录 ID")
        child.add_argument("--from-draft", dest="from_draft", type=Path,
                           help="对话入口写出的草稿目录（含 contract.json，可选 record.json）")
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
        if command == "report":
            child.add_argument("--full", action="store_true", help="按需生成完整分节报告（不入库）")
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
    elif command == "base-build-config":
        child.add_argument("--commands-json", required=True,
                           help="构建命令 argv 数组的 JSON；[] 明确表示无构建步骤")
        child.add_argument("--confirmed-by", required=True, help="具名人工确认人")


def _maintenance_arguments(child, command):
    if command == "requirements":
        child.add_argument("--id", dest="registry_id", help="只看一个需求、规则或例子的版本历史")
    if command == "map-apply":
        child.add_argument("--proposal", required=True, help="map-propose 给出的草稿摘要")
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
                           help="base 实现者；仅支持人工通道（默认）")
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
