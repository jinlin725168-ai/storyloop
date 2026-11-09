"""Bench command wiring; real agent execution stays behind an explicit gate."""

from __future__ import annotations

import json
from pathlib import Path
import sys

from .bench_calibrate import calibrate_case
from .models import require


def add_bench_arguments(sub) -> None:
    bench = sub.add_parser("bench", help="冻结 Case 校准与评测记录")
    bench.add_argument("--json", action="store_true")
    commands = bench.add_subparsers(dest="bench_command", required=True)
    calibrate = commands.add_parser("calibrate", help="只执行 Gold，不调用代理")
    calibrate.add_argument("case_id")
    calibrate.add_argument("--case-version", type=int, default=1)
    calibrate.add_argument("--repository", type=Path, required=True)
    calibrate.add_argument("--python", type=Path, help="确定性 Gold 检查使用的 Python；默认当前 StoryLoop 解释器")
    calibrate.add_argument("--cases-root", type=Path, default=Path("bench/cases"))
    calibrate.add_argument("--output-root", type=Path, default=Path.cwd())
    report = commands.add_parser("report", help="读取冻结 Run 记录")
    report.add_argument("run_id")
    report.add_argument("--output-root", type=Path, default=Path.cwd())
    run = commands.add_parser("run", help="创建真实参测 Run（需明确接受成本）")
    run.add_argument("case_id", nargs="+")
    run.add_argument("--case-version", type=int, default=1)
    run.add_argument("--adapter", choices=("codex", "claude"), required=True)
    run.add_argument("--model", required=True)
    run.add_argument("--scope", choices=("paired", "base-only"), default="paired")
    run.add_argument("--suite-mode", choices=("generated", "frozen"), default="generated",
                     help="默认由 acceptance 生成套件；frozen 只隔离评测实现链路")
    run.add_argument("--base-engine-commit", required=True)
    run.add_argument("--candidate-engine-commit")
    run.add_argument("--engine-repository", type=Path, required=True)
    run.add_argument("--repository", action="append", default=[], metavar="ID=PATH", required=True)
    run.add_argument("--engine-python", type=Path, required=True,
                     help="已准备依赖的隔离 venv Python")
    run.add_argument("--gold-python", type=Path)
    run.add_argument("--agent-command", nargs="+", help="Claude 使用的 StoryLoop command 适配器 argv")
    run.add_argument("--max-calls", type=int, default=20)
    run.add_argument("--max-wall-seconds", type=int, default=14400)
    run.add_argument("--cases-root", type=Path, default=Path("bench/cases"))
    run.add_argument("--output-root", type=Path, default=Path.cwd())
    run.add_argument("--i-accept-cost", action="store_true")
    run.add_argument("--yes", action="store_true", help="确认所打印的运行次数与成本提示")


def dispatch_bench(args) -> dict:
    if args.bench_command == "calibrate":
        require(args.case_version in {1, 2}, "Only v1/v2 Case data is available")
        source = args.cases_root / args.case_id / f"v{args.case_version}"
        return calibrate_case(source, args.repository, args.output_root, python=args.python)
    if args.bench_command == "report":
        require(args.run_id.isalnum(), "Invalid Run ID")
        path = args.output_root / ".storyloop/bench/runs" / f"{args.run_id}.json"
        require(path.is_file() and not path.is_symlink(), "Run record is unavailable")
        return json.loads(path.read_text())
    return _run(args)


def _run(args) -> dict:
    from .bench_case import load_case
    from .bench_real_exec import run_isolated_attempt
    from .bench_run import run_bench

    require(args.i_accept_cost, "真实评测默认关闭；预计 paired 累计 13～22 小时，需 --i-accept-cost")
    require(args.scope == "base-only" or args.candidate_engine_commit,
            "paired 需要 --candidate-engine-commit")
    require(args.adapter != "claude" or args.agent_command and "{model}" in args.agent_command,
            "Claude 需要含 {model} 的 --agent-command（StoryLoop JSON transport）")
    repositories = {}
    for item in args.repository:
        identity, separator, path = item.partition("=")
        require(separator and identity and path and identity not in repositories,
                "--repository 必须是唯一的 ID=PATH")
        repositories[identity] = Path(path).resolve()
    sources = [args.cases_root / name / f"v{args.case_version}" for name in args.case_id]
    cases = [load_case(source) for source in sources]
    require(all(case["manifest"]["repository_id"] in repositories for case in cases),
            "每个 Case 必须指定来源 repository_id 的只读仓库")
    count = len(cases) * 2 * (1 if args.scope == "base-only" else 2)
    estimate = "约 6.5～11 小时" if args.scope == "base-only" else "约 13～22 小时"
    print(f"真实 bench：{count} 次；预计累计 {estimate}；适配器 {args.adapter}；模型 {args.model}",
          file=sys.stderr)
    if not args.yes:
        require(sys.stdin.isatty(), "需再次确认：交互输入 yes 或传入 --yes")
        require(input("输入 yes 才开始真实评测：").strip() == "yes", "真实评测未获再次确认")
    budget = {"max_calls": args.max_calls, "max_wall_seconds": args.max_wall_seconds}

    def invoke(side, case, attempt, human):
        commit = args.base_engine_commit if side == "base" else args.candidate_engine_commit
        return run_isolated_attempt(case, repositories[case["manifest"]["repository_id"]], commit,
                                    args.adapter, args.model, budget, human,
                                    engine_repository=args.engine_repository,
                                    engine_python=args.engine_python,
                                    gold_python=args.gold_python,
                                    agent_command=args.agent_command,
                                    suite_mode=args.suite_mode)

    return run_bench(sources, args.base_engine_commit, args.candidate_engine_commit or "",
                     args.adapter, args.model, budget, invoke, None, args.output_root,
                     i_accept_cost=True, gold_python=args.gold_python, scope=args.scope,
                     confirmed=True, suite_mode=args.suite_mode)
