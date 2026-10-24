"""Pure Markdown rendering for a frozen report presentation."""

from __future__ import annotations

import html

from .report_markdown_sections import legend, render_reader_section


SOURCE_LABELS = {
    "machine": "🔧 机器",
    "human": "👤 人工",
    "agent_code": "🤖 代理代码分析",
    "agent_sample": "🤖 代理样例",
}
LIGHT_SECTIONS = frozenset({"0", "1", "3"})


def render_presentation(report: dict) -> str:
    """Render only the supplied frozen snapshot; never collect live state."""
    presentation = report["presentation"]
    tier = presentation.get("tier")
    identity = _identity(presentation)
    change_id = identity.get("change_id") or report.get("change_id") or "-"
    lines = [f"# StoryLoop 验收报告：{_text(change_id)}", "", *legend()]
    for key in sorted(presentation["sections"]):
        if tier == "light" and key not in LIGHT_SECTIONS:
            continue
        section = presentation["sections"][key]
        lines.extend([f"## {key}. {_text(section.get('title'))}", ""])
        reader_lines = render_reader_section(key, section)
        if reader_lines is not None:
            lines.extend(reader_lines)
            continue
        if key == "3":
            lines.extend(_oracle_compat(section))
        for entry in section.get("items") or []:
            lines.extend(_render_item(entry, report))
    return "\n".join(lines).rstrip() + "\n"


def _identity(presentation: dict) -> dict:
    items = presentation.get("sections", {}).get("0", {}).get("items", [])
    entry = next((item for item in items if item.get("id") == "identity"), {})
    return entry.get("data") or {}


def _render_item(entry: dict, report: dict) -> list[str]:
    identifier = entry.get("id", "-")
    source = SOURCE_LABELS.get(entry.get("source"), _text(entry.get("source")))
    availability = entry.get("availability", "NOT_AVAILABLE")
    merged = identifier == "merge_commit" and report.get("merge_commit")
    data = report.get("merge_commit") if merged else entry.get("data")
    availability = "AVAILABLE" if merged else availability
    title = {"layers": "layers（分层验证）", "role_calls": "角色档次（适配器回报）",
             "role_protection": "中间防线与子代理（适配器回报，非机器验证）",
             "role_downgrades": "判定角色降档理由"}.get(identifier, identifier)
    lines = [f"### {_text(title)}", "", f"- 来源：{source}", f"- 可用性：`{_text(availability)}`"]
    if entry.get("reason") and not merged:
        lines.append(f"- 说明：`{_text(entry['reason'])}`")
    if isinstance(data, dict) and data.get("verification") == "CONTENT_NOT_MACHINE_VERIFIED":
        lines.append("- 作者：未知（未持久化可靠作者来源）")
        lines.append("- 内容验证：未经过机器事实验证（`CONTENT_NOT_MACHINE_VERIFIED`）")
    lines.append("")
    if identifier == "role_calls" and isinstance(data, list):
        lines.extend(_role_calls(data))
    elif identifier == "cost" and isinstance(data, dict):
        lines.extend(_cost(data))
    elif identifier == "cost" and data is None:
        lines.extend(["未提供", "- 版本漂移：未知（缺少版本快照或执行画像）"])
    elif identifier in {"baseline", "executed", "uncounted"}:
        lines.extend(_oracle(identifier, data))
    else:
        lines.extend(_value(data))
    return lines + [""]


def _value(value: object, depth: int = 0) -> list[str]:
    if value is None:
        return ["未提供"]
    if isinstance(value, dict):
        return _mapping(value, depth)
    if isinstance(value, list):
        if not value:
            return ["（空）"]
        lines = []
        for index, item in enumerate(value, 1):
            if isinstance(item, (dict, list)):
                lines.append(f"{index}.")
                lines.extend("   " + line for line in _value(item, depth + 1))
            else:
                lines.append(f"{index}. {_text(item)}")
        return lines
    return [_text(value)]


def _mapping(value: dict, depth: int) -> list[str]:
    if not value:
        return ["（空）"]
    lines = []
    for key, item in value.items():
        label = _text(key)
        if isinstance(item, (dict, list)):
            lines.append(f"- {label}：")
            lines.extend("  " + line for line in _value(item, depth + 1))
        else:
            lines.append(f"- {label}：{_text(item) if item is not None else '未提供'}")
    return lines


def _cost(data: dict) -> list[str]:
    total = data.get("cost_usd")
    subtotal = data.get("known_cost_usd")
    lines = [f"- 调用：{_text(data.get('calls', 0))}", f"- 失败：{_text(data.get('failed', 0))}",
             f"- 已知费用小计：{_money(subtotal)}",
             f"- 完整费用总计：{_money(total) if total is not None else '未提供'}"]
    coverage = (data.get("coverage") or {}).get("cost_usd")
    if coverage:
        lines.append(f"- 费用覆盖：{_text(coverage.get('known'))}/{_text(coverage.get('total'))}")
    if (drift := data.get("version_drift")):
        status = drift.get("status")
        if status == "unchanged":
            detail = "无"
        elif status == "changed":
            parts = []
            if drift.get("engine_versions"):
                parts.append("引擎 " + "、".join(drift["engine_versions"]))
            if drift.get("skills_changed"):
                parts.append("技能 " + "、".join(drift["skills_changed"]))
            detail = "运行中版本有变化（" + "；".join(parts) + "）"
        else:
            detail = "未知（缺少版本快照或执行画像）"
        if drift.get("unprofiled_calls", 0) > 0:
            detail += f"，其中 {drift['unprofiled_calls']} 次调用没有执行画像"
        lines.append(f"- 版本漂移：{detail}")
    for key in ("by_role", "input_tokens", "output_tokens", "turns", "models", "vendors", "tiers"):
        if key in data:
            lines.extend([f"- {_text(key)}：", *["  " + line for line in _value(data[key])]])
    return lines


def _oracle(identifier: str, data: object) -> list[str]:
    del identifier
    return _value(data)


def _oracle_compat(section: dict) -> list[str]:
    items = {item.get("id"): item.get("data") for item in section.get("items") or []}
    baseline, executed = items.get("baseline") or {}, items.get("executed") or {}
    lines = ["## 判据自证", "", "| 层 | 命令 / 用例 | 执行数 | 基线 | 解析器 |",
             "|---|---|---|---|---|"]
    for layer, records in executed.items():
        before = baseline.get(layer) or []
        for index, record in enumerate(records or []):
            count = "未能统计" if record.get("executed") is None else record.get("executed")
            prior = before[index] if index < len(before) else "-"
            lines.append(f"| {_cell(layer)} | {_cell(record.get('id'))} | {_cell(count)} | "
                         f"{_cell(prior)} | {_cell(record.get('parser') or '-')} |")
    legacy = items.get("skills_legacy") or {}
    skills = ", ".join(f"{_cell(name)} {_cell(version)}" for name, version in sorted(legacy.items()))
    return lines + ["", f"使用的技能包：{skills or '-'}", ""]


def _money(value: object) -> str:
    return "未提供" if value is None else f"${value}"


def _text(value: object) -> str:
    escaped = html.escape(str(value if value is not None else "-"), quote=False)
    for char in ("\\", "`", "*", "[", "]", "#", "|"):
        escaped = escaped.replace(char, "\\" + char)
    return escaped.replace("\r", " ").replace("\n", "<br>")


def _cell(value: object) -> str:
    raw = str(value if value is not None else "-").replace("\r", " ").replace("\n", " ")
    escaped = html.escape(raw, quote=False)
    for char in ("\\", "`", "*", "[", "]", "#", "|"):
        escaped = escaped.replace(char, "\\" + char)
    return escaped


def _role_calls(rows):
    lines = ["| 角色 / 调用 | 适配器 | 模型 / 厂商 | 请求档次 → 实际档次 | 状态 | 可进档次统计 |",
             "|---|---|---|---|---|---|"]
    for row in rows:
        lines.append(f"| {_cell(row.get('role'))} / {_cell(row.get('call'))} | "
                     f"{_cell(row.get('adapter'))} | {_cell(row.get('model'))} / {_cell(row.get('vendor'))} | "
                     f"{_cell(row.get('tier_requested'))} → {_cell(row.get('tier_applied'))} | "
                     f"{_cell(row.get('status'))} | {_cell(row.get('statistics_eligible'))} |")
    return lines
