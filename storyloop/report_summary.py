"""Report v2 summary (roadmap F1, issue #64): what a person reads in Git.

Rendered from the report's raw evidence alone, so the merge receipt can re-render it byte for byte
without the project. The full sectioned report is rebuilt on demand (`storyloop report --full`)
from the same evidence and never committed.
"""
from __future__ import annotations

from .processutil import redact


def render_summary(report: dict) -> str:
    change_id = report["change_id"]
    routing = report.get("routing") or {}
    telemetry = report.get("telemetry") or {}
    review = report.get("review") or {}
    lines = [f"# StoryLoop 验收报告：{change_id}", "",
             f"- 状态：{report['status']}",
             f"- 档位：{routing.get('effective') or '未分流'}",
             f"- 目标：{_cell(report['requirement']['goal'])}",
             f"- 合同摘要：`{report['requirement']['approval']['contract_digest']}`",
             f"- 实现提交：`{report['implementation_commit']}`"]
    if report.get("merge_commit"):
        lines.append(f"- 合并提交：`{report['merge_commit']}`")
    lines += ["", "## 验收追溯", "", "| 验收条件 | 测试 | 结果 |", "|---|---|---|"]
    for item in report.get("traceability") or []:
        tests = item.get("tests") or []
        names = "、".join(test.get("id", "?") for test in tests) or "人工确认"
        results = "、".join((test.get("result") or {}).get("status", "?") for test in tests) or "—"
        lines.append(f"| {item['criterion']['id']} {_cell(item['criterion'].get('then', ''))} | {names} | {results} |")
    lines += ["", "## 分层验证", "", "| 层 | 结果 | 执行数 |", "|---|---|---|"]
    for name, layer in (report.get("layers") or {}).items():
        executed = sum((result.get("counts") or {}).get("executed") or 0 for result in layer.get("results") or [])
        lines.append(f"| {name} | {layer.get('status')} | {executed or '—'} |")
    lines += _oracle(report.get("oracle") or {})
    findings = review.get("findings") or []
    lines += ["", "## 审查", "", f"- 结论：{review.get('verdict')}，意见 {len(findings)} 条",
              f"- 摘要：{_cell(review.get('summary', ''))[:400]}"]
    correction = report.get("correction") or {}
    if correction.get("failures") or correction.get("overrules"):
        lines.append(f"- 返工：失败 {len(correction.get('failures') or [])} 次，"
                     f"人工推翻 {len(correction.get('overrules') or [])} 次")
    cost = telemetry.get("known_cost_usd")
    lines += ["", "## 成本", "",
              f"- 代理调用 {telemetry.get('calls', '—')} 次，实现尝试 {telemetry.get('attempts', '—')} 轮",
              f"- 代理耗时 {_minutes(telemetry.get('agent_ms'))}，已知费用 "
              + (f"${cost:.2f}" if isinstance(cost, (int, float)) else "未知")]
    lines += ["", "完整报告（分节证据、命令、过程与时间线）不入库，按需生成：",
              f"`storyloop report <项目> --change {change_id} --full`", ""]
    return "\n".join(lines)


def _oracle(oracle: dict) -> list[str]:
    """Executed counts against the pre-change baseline: the tests really ran, and grew."""
    executed, baseline = oracle.get("executed") or {}, oracle.get("baseline") or {}
    lines = ["", "## 判据自证", "", "| 层 | 命令 / 用例 | 执行数 | 基线 | 解析器 |", "|---|---|---|---|---|"]
    for layer, records in executed.items():
        before = baseline.get(layer) or []
        for index, record in enumerate(records or []):
            count = "未能统计" if record.get("executed") is None else record.get("executed")
            prior = before[index] if index < len(before) and before[index] is not None else "-"
            lines.append(f"| {_cell(layer)} | {_cell(record.get('id'))} | {_cell(count)} | "
                         f"{_cell(prior)} | {_cell(record.get('parser') or '-')} |")
    skills = oracle.get("skills_used") or {}
    if skills:
        lines += ["", "使用的技能包：" + ", ".join(f"{name} {version}" for name, version in sorted(skills.items()))]
    return lines


def _cell(value: object) -> str:
    return redact(str(value)).replace("|", "\\|").replace("\n", " ").strip()


def _minutes(value: object) -> str:
    return f"{value / 60000:.1f} 分钟" if isinstance(value, (int, float)) else "未知"
