"""Deterministic Markdown presentation for cross-change metrics."""

from __future__ import annotations

from collections import Counter
import json
import math


COMMON = {"id", "value", "numerator", "denominator", "n", "changes", "missing", "reason"}


def render_metrics(result: dict) -> str:
    """Render every metric and evidence state without converting absence to zero."""
    lines = ["# 外环指标", "", _heading(result), "", "## 指标", "",
             "| 指标 | 值 | 样本 | 说明 |", "|---|---:|---:|---|"]
    rows = _metric_rows(result.get("metrics"))
    lines.extend(rows or ["| — | 无数据（NO_SAMPLE） | 0/0；n=0 | 无数据 |"])
    fired, insufficient, unavailable = _suggestion_groups(result.get("suggestions"))
    _append_list(lines, "调整建议", [_suggestion(item) for item in fired], "无")
    metrics = result.get("metrics")
    m10 = metrics.get("M10", {}) if isinstance(metrics, dict) else {}
    _append_list(lines, "自动化方向参考", _intervention_counts(m10.get("value"))[:3], "无")
    _append_list(lines, "样本不足", [_insufficient(item) for item in insufficient], "无")
    _append_list(lines, "规则证据不可用", [_unavailable(item) for item in unavailable], "无")
    _append_list(lines, "延后项信号", [_deferred(item) for item in _list(result.get("deferred"))], "无")
    _append_list(lines, "需人工判断的延后项",
                 [_safe(item) for item in _list(result.get("manual_deferred"))], "无")
    _append_list(lines, "缺数据汇总", _missing_summary(result), "无")
    _append_list(lines, "证据差异", [_discrepancy(item) for item in _list(
        result.get("discrepancies"))], "无")
    lines.extend(["", "以上建议由人决定是否采纳"])
    return "\n".join(lines)


def _heading(result: dict) -> str:
    count = result.get("changes") if type(result.get("changes")) is int else 0
    since = result.get("since")
    generated = _safe(result.get("generated_at", "未知"))
    scope = f"自 {_safe(since)} 起" if isinstance(since, str) else "全部记录"
    return f"变更数：{count}；时间范围：{scope}；生成时间：{generated}"


def _metric_rows(value: object) -> list[str]:
    if not isinstance(value, dict):
        return []
    rows = []
    for metric_id, metric in value.items():
        if metric_id == "M12" and isinstance(metric, dict):
            rows.extend(_metric_row(f"M12.{name}", row) for name, row in metric.items()
                        if isinstance(row, dict))
        elif isinstance(metric, dict):
            rows.append(_metric_row(str(metric_id), metric))
    return rows


def _metric_row(metric_id: str, metric: dict) -> str:
    value = metric.get("value")
    shown = _number(value) if value is not None else f"无数据（{_metric_reason(metric)}）"
    if metric_id == "M10" and isinstance(value, dict):
        shown = "；".join(_intervention_counts(value)) or "0"
    sample = f"{_number(metric.get('numerator'))}/{_number(metric.get('denominator'))}；n={_number(metric.get('n'))}"
    details = []
    changes = _list(metric.get("changes"))
    if changes:
        details.append("变更=" + ", ".join(_safe(item) for item in changes))
    missing = _list(metric.get("missing"))
    if missing:
        details.append("缺失=" + _compact(missing))
    extras = {key: value for key, value in metric.items() if key not in COMMON}
    if extras:
        details.append(_compact(extras))
    return f"| {_safe(metric_id)} | {_safe(shown)} | {_safe(sample)} | {_safe('；'.join(details) or '—')} |"


def _intervention_counts(value: object) -> list[str]:
    if not isinstance(value, dict):
        return []
    counts = [(name, count) for name, count in value.items() if type(count) is int and count > 0]
    return [f"{name}={count}" for name, count in sorted(counts, key=lambda item: -item[1])]


def _metric_reason(metric: dict) -> str:
    reason = metric.get("reason")
    if isinstance(reason, str) and reason:
        return reason
    for item in _list(metric.get("missing")):
        if isinstance(item, dict) and isinstance(item.get("reason"), str):
            return item["reason"]
    return "NO_SAMPLE"


def _suggestion_groups(value: object) -> tuple[list[dict], list[dict], list[dict]]:
    suggestions = [item for item in _list(value) if isinstance(item, dict)]
    fired = [item for item in suggestions if "text" in item]
    insufficient = [item for item in suggestions if "insufficient" in item]
    unavailable = [item for item in suggestions if "unavailable" in item]
    return fired, insufficient, unavailable


def _suggestion(item: dict) -> str:
    parts = [f"{_safe(item.get('rule'))}: 触发值 {_number(item.get('value'))}",
             f"阈值 {_number(item.get('threshold'))}", f"n={_number(item.get('n'))}"]
    evidence = ", ".join(_safe(value) for value in _list(item.get("evidence"))) or "无"
    more = item.get("more") if type(item.get("more")) is int else 0
    parts.append(f"证据 {evidence}" + (f"（另有 {more} 个）" if more else ""))
    labels = (("group", "分组"), ("rule_ids", "规则 ID"), ("layers", "CI 层"),
              ("tiers", "档次"), ("rules_unavailable", "规则证据不可用"),
              ("metric", "子指标"), ("direction", "方向"))
    for key, label in labels:
        value = item.get(key)
        if value is not None:
            rendered = ", ".join(_safe(entry) for entry in value) if isinstance(value, list) else _safe(value)
            parts.append(f"{label} {rendered}")
    parts.append(_safe(item.get("text")))
    return "；".join(parts)


def _insufficient(item: dict) -> str:
    suffix = f"（{_safe(item.get('metric'))}）" if item.get("metric") else ""
    return f"{_safe(item.get('rule'))}{suffix}: {_safe(item.get('insufficient'))}"


def _unavailable(item: dict) -> str:
    return f"{_safe(item.get('rule'))}: {_safe(item.get('unavailable'))}"


def _deferred(item: object) -> str:
    if not isinstance(item, dict):
        return _safe(item)
    state = "已触发" if item.get("triggered") is True else "未触发"
    parts = [f"{_safe(item.get('item'))}: {state}"]
    if item.get("unavailable"):
        parts.append("不可用=" + _safe(item["unavailable"]))
    evidence = _list(item.get("evidence"))
    if evidence:
        parts.append("证据=" + ", ".join(_safe(value) for value in evidence))
    return "；".join(parts)


def _missing_summary(result: dict) -> list[str]:
    missing = list(_list(result.get("missing")))
    metrics = result.get("metrics")
    if isinstance(metrics, dict):
        for metric in metrics.values():
            if isinstance(metric, dict) and metric.get("id") == "M12":
                for row in metric.values():
                    if isinstance(row, dict):
                        missing.extend(_list(row.get("missing")))
            elif isinstance(metric, dict):
                missing.extend(_list(metric.get("missing")))
    counts = Counter(item.get("reason") for item in missing
                     if isinstance(item, dict) and isinstance(item.get("reason"), str))
    return [f"{_safe(reason)}: {count}" for reason, count in sorted(counts.items())]


def _discrepancy(item: object) -> str:
    if not isinstance(item, dict):
        return _safe(item)
    return (f"{_safe(item.get('change_id'))}.{_safe(item.get('field'))}: "
            f"report={_compact(item.get('report'))}；run={_compact(item.get('run'))}")


def _append_list(lines: list[str], title: str, items: list[str], empty: str) -> None:
    lines.extend(["", f"## {title}", ""])
    lines.extend(f"- {item}" for item in (items or [empty]))


def _compact(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return _safe(value)


def _number(value: object) -> str:
    if type(value) is int:
        return str(value)
    if type(value) is float and math.isfinite(value):
        return format(value, ".4g")
    return "无数据"


def _safe(value: object) -> str:
    text = str(value) if value is not None else "无"
    return " ".join(text.splitlines()).replace("\\", "\\\\").replace("|", "\\|")


def _list(value: object) -> list:
    return value if isinstance(value, list) else []
