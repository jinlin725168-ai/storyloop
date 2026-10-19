"""Reader-oriented templates for decision, review-focus and process sections."""

from __future__ import annotations

import html
import re


SOURCE_LABELS = {
    "machine": "🔧 机器",
    "human": "👤 人工",
    "agent_code": "🤖 代理代码分析",
    "agent_sample": "🤖 代理样例",
}
PROCESS_LABELS = {
    "failures": "失败记录", "agent_failures": "代理失败", "repairs": "修复记录",
    "approvals": "人工批准", "overrules": "人工驳回", "resets": "重置记录", "reset": "当前重置",
    "answers": "人工回答", "product_approval": "产品批准", "review": "独立审查",
    "escalations": "升档记录", "chronology": "完整时间线", "fact_history": "事实演变历史",
    "continue_heavy": "继续重档确认", "design": "设计说明", "design_approval": "设计批准",
}


def legend() -> list[str]:
    return ["来源图例：", "", "- 🔧 machine：机器可验证事实或确定性计算", "- 👤 human：人工记录",
            "- 🤖 agent_code：代理代码分析", "- 🤖 agent_sample：代理样例", ""]


def render_reader_section(key: str, section: dict) -> list[str] | None:
    items = {entry.get("id"): entry for entry in section.get("items") or []}
    if key == "1":
        return _conclusion(items)
    if key == "2":
        return _focus(items)
    if key == "4":
        return _process(section.get("items") or [])
    return None


def _conclusion(items: dict) -> list[str]:
    lines = _statement("目标", items.get("goal"))
    completion = _data(items.get("completion")) or {}
    lines += ["### 完成情况", "", f"完成进度：{inline(completion.get('completed'))}/{inline(completion.get('total'))}", ""]
    lines += _gates(_data(items.get("gates")))
    lines += _traceability(_data(items.get("ac_matrix")))
    for identifier, title in (("non_goals", "非目标"), ("manual_confirmations", "人工确认"),
                              ("reconciliation", "运行对账"), ("discovered", "发现测试数"),
                              ("added", "新增测试数")):
        lines += _summary(title, items.get(identifier))
    return lines


def _gates(data: object) -> list[str]:
    if not isinstance(data, dict):
        return ["### 门禁", "", "未提供", ""]
    lines = ["### 门禁", "", "| 门禁层 | 状态 |", "|---|---|"]
    lines += [f"| {cell(name)} | {cell(status)} |" for name, status in (data.get("layers") or {}).items()]
    lines += ["", f"通过 {inline(data.get('passed'))}/{inline(data.get('total'))}；"
              f"跳过 {inline(data.get('skipped'))}；审查结论 {inline(data.get('review_verdict'))}。", ""]
    return lines


def _traceability(rows: object) -> list[str]:
    lines = ["### 验收追溯", "", "| 验收条件 | 测试 | 结果 | 证据 SHA-256 |", "|---|---|---|---|"]
    for row in rows if isinstance(rows, list) else []:
        tests = row.get("tests") or []
        lines.append(f"| {cell(row.get('criterion_id'))} | {cell(', '.join(str(t.get('id')) for t in tests))} | "
                     f"{cell(', '.join(str(t.get('status')) for t in tests))} | {cell(row.get('evidence_sha256'))} |")
    return lines + [""]


def _focus(items: dict) -> list[str]:
    lines = _paths(_data(items.get("paths")))
    plan = _data(items.get("plan_paths")) or {}
    lines += ["### 计划偏差", "", f"未计划变更：{_joined(plan.get('unplanned'))}",
              f"计划但未变更：{_joined(plan.get('not_changed'))}", ""]
    lines += _risks(_data(items.get("risks")))
    lines += _gaps(_data(items.get("layer_gaps")))
    lines += _commands(_data(items.get("verification")))
    lines += _summary("人工验证引用", items.get("human_verification"))
    for identifier in ("runtime_concurrency", "expiring_constants", "semantic_decisions",
                       "refactor_behavior_differences", "historical_review_issues", "performance"):
        if identifier in items:
            lines += _summary(identifier, items[identifier])
    return lines


def _paths(rows: object) -> list[str]:
    lines = ["### 变更路径", "", "| 路径 | 状态 | 优先级 | 仅空白变更 |", "|---|---|---|---|"]
    for row in rows if isinstance(rows, list) else []:
        lines.append(f"| {cell(row.get('path'))} | {cell(row.get('status'))} | {cell(row.get('priority'))} | "
                     f"{cell(row.get('whitespace_only'))} |")
    return lines + [""]


def _risks(rows: object) -> list[str]:
    lines = ["### 风险覆盖", ""]
    if not isinstance(rows, list):
        return lines + ["未提供", ""]
    for row in rows:
        prefix = "未覆盖风险" if row.get("coverage") == "UNCOVERED" else "已关联风险"
        lines.append(f"- {prefix}：{inline(row.get('risk'))}；判据 {_joined(row.get('criteria'))}；测试 {_joined(row.get('tests'))}")
    return lines + [""]


def _gaps(rows: object) -> list[str]:
    lines = ["### 验证缺口", ""]
    for row in rows if isinstance(rows, list) else []:
        lines.append(f"- 验证缺口：{inline(row.get('layer'))}；{inline(row.get('kind'))}；"
                     f"原因 {inline(row.get('reason_code'))}；记录 {inline(row.get('record'))}")
    return lines + (["未发现", ""] if len(lines) == 2 else [""])


def _commands(rows: object) -> list[str]:
    lines = ["### 可复制验证命令", ""]
    for row in rows if isinstance(rows, list) else []:
        lines += [f"#### {inline(row.get('criterion_id'))} / {inline(row.get('test_id'))}", "",
                  fenced(str(row.get("command") or ""), "sh"), ""]
    return lines + (["未提供", ""] if len(lines) == 2 else [])


def _process(entries: list[dict]) -> list[str]:
    lines = []
    for entry in entries:
        identifier = entry.get("id", "-")
        title = "事实声明" if identifier.startswith("facts:") else PROCESS_LABELS.get(identifier, identifier)
        if identifier == "answers":
            lines += _answers(entry)
        elif identifier == "review":
            lines += _review(entry)
        elif identifier == "design" or identifier.startswith("facts:"):
            lines += _statement(title, entry)
        else:
            lines += _summary(title, entry)
    return lines


def _answers(entry: dict) -> list[str]:
    lines = ["### 人工回答", "", *_meta(entry)]
    records = _data(entry)
    for record in records if isinstance(records, list) else []:
        lines.append(f"- 问题：{inline(record.get('question'))}；回答：{inline(record.get('answer'))}")
    return lines + (["未提供"] if not isinstance(records, list) else []) + [""]


def _review(entry: dict) -> list[str]:
    lines = ["### 独立审查", "", *_meta(entry)]
    data = _data(entry)
    if not isinstance(data, dict):
        return lines + ["未提供", ""]
    lines += [f"- 结论：{inline(data.get('verdict'))}", f"- 摘要：{inline(data.get('summary'))}"]
    if data.get("findings"):
        lines.append(f"- 发现：{inline(data['findings'])}")
    if data.get("questions"):
        lines.append(f"- 问题：{inline(data['questions'])}")
    return lines + [""]


def _statement(title: str, entry: dict | None) -> list[str]:
    data = _data(entry)
    value = data.get("value") if isinstance(data, dict) else None
    lines = [f"### {title}", "", *_meta(entry)]
    if isinstance(data, dict) and data.get("verification") == "CONTENT_NOT_MACHINE_VERIFIED":
        lines += ["作者：未知（未持久化可靠作者来源）  ",
                  "内容验证：未经过机器事实验证（`CONTENT_NOT_MACHINE_VERIFIED`）", ""]
    if title == "事实声明" and isinstance(value, dict):
        details = [f"- 声明：{inline(value.get('statement'))}"]
        for key, label in (("location", "位置"), ("role", "角色"), ("attempt", "轮次")):
            if value.get(key) is not None:
                details.append(f"- {label}：{inline(value[key])}")
        return lines + details + [""]
    return lines + _readable(value) + [""]


def _summary(title: str, entry: dict | None) -> list[str]:
    return [f"### {title}", "", *_meta(entry), *_readable(_data(entry)), ""]


def _meta(entry: dict | None) -> list[str]:
    if not entry:
        return ["可用性：`NOT_AVAILABLE`", ""]
    source = SOURCE_LABELS.get(entry.get("source"), inline(entry.get("source")))
    lines = [f"来源：{source}；可用性：`{inline(entry.get('availability'))}`"]
    if entry.get("reason"):
        lines.append(f"说明：`{inline(entry.get('reason'))}`")
    return lines + [""]


def _readable(value: object) -> list[str]:
    if value is None:
        return ["未提供"]
    if isinstance(value, list):
        return [f"- {inline(item)}" for item in value] if value else ["（空）"]
    if isinstance(value, dict):
        return [f"- {inline(key)}：{inline(item)}" for key, item in value.items()] if value else ["（空）"]
    return [inline(value)]


def _data(entry: dict | None) -> object:
    return entry.get("data") if entry else None


def _joined(value: object) -> str:
    return "、".join(inline(item) for item in value) if isinstance(value, list) and value else "无"


def fenced(value: str, language: str = "") -> str:
    longest = max((len(run) for run in re.findall(r"`+", value)), default=0)
    marker = "`" * max(3, longest + 1)
    return f"{marker}{language}\n{value}\n{marker}"


def inline(value: object) -> str:
    raw = str(value if value is not None else "-").replace("\r", " ").replace("\n", " ")
    escaped = html.escape(raw, quote=False)
    for char in ("\\", "`", "*", "[", "]", "#", "|"):
        escaped = escaped.replace(char, "\\" + char)
    return escaped


def cell(value: object) -> str:
    return inline(value)
