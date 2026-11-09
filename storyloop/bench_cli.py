"""Bench command wiring; real agent execution stays behind an explicit gate."""

from __future__ import annotations

import json
from pathlib import Path

from .bench_calibrate import calibrate_case
from .models import StoryLoopError, require


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
    run.add_argument("--i-accept-cost", action="store_true")


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
    if not args.i_accept_cost:
        raise StoryLoopError("真实评测默认关闭；预计累计 13～22 小时。若将来执行，请显式传入 --i-accept-cost")
    raise StoryLoopError("真实代理适配器尚未接入；当前仅支持测试适配器验证 Run 调度")
