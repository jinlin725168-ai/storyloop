"""Human-readable and complete JSON output for CLI results."""

from __future__ import annotations

import json

from .change_log import render_log
from .memory_cli import render_memory
from .metrics_render import render_metrics
from .stop_report import render_stop


def display(result: dict, complete_json: bool) -> None:
    if complete_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if "run_id" in result and "cases" in result and "summary" in result:
        print(_render_bench_report(result))
        return
    if result.get("status") == "LOG":
        print(render_log(result))
        return
    if result.get("status") == "OVERVIEW":
        from .status_overview import render_overview
        print(render_overview(result))
        return
    if result.get("status") == "METRICS":
        print(render_metrics(result))
        return
    if result.get("status") == "REQUIREMENTS":
        from .requirements_registry import render_view
        print(render_view(result))
        return
    rendered = render_memory(result)
    if rendered is not None:
        print(rendered)
        return
    print(result.get("status", "REPORT"))
    for key in ("change_id", "project", "next", "report", "pending", "accepted", "paths", "written"):
        if key in result and result[key] is not None:
            print(f"{key}: {result[key]}")
    if result.get("readiness_status") or ("layers" in result and "binding" in result):
        for name, layer in result.get("layers", {}).items():
            if layer.get("status") != "FAILED":
                continue
            print(f"{name}: {layer['status']}")
            for item in layer.get("results", []):
                if item.get("status") == "PASS" or not item.get("failure_summary"):
                    continue
                print(f"{name}: {item.get('id', 'command')}")
                for heading in item["failure_summary"]:
                    print(f"  {heading}")
    if isinstance(result.get("stop"), dict):
        for line in render_stop(result["stop"]):
            print(line)
    for warning in result.get("warnings", []):
        print(f"warning: {warning}")


def _render_bench_report(record: dict) -> str:
    summary = record["summary"]
    revision = record.get("case_revision") or record["case_version"]
    lines = [
        f"Run {record['run_id']}（{revision}，{record['scope']}，{record['suite_source']}）",
        f"通过 {summary['passed']}/{summary['attempts']}，Gold 失败 {summary['gold_failed']}，"
        f"非例行介入合计 {summary['nonroutine_total']}",
    ]
    for case in summary["cases"]:
        verdicts = "/".join(case["verdicts"])
        nonroutine = "/".join(str(value) for value in case["nonroutine"])
        lines.append(f"{case['case_id']}：{verdicts}（非例行 {nonroutine}）")
    stops = sorted(summary["stop_reasons"].items(), key=lambda item: (-item[1], item[0]))
    lines.append("停点：" + "，".join(f"{reason}×{count}" for reason, count in stops))
    if summary["stage_gate_passed"] is not None:
        lines.append("门槛：通过" if summary["stage_gate_passed"] else "门槛：未通过")
    return "\n".join(lines)
