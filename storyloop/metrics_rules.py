"""Calibration rules over computed metrics and read-only deferred-item signals."""

from __future__ import annotations

from copy import deepcopy
import math

from .models import StoryLoopError


DEFAULTS: dict[str, dict] = {
    "R1": {"threshold": 0.2, "min_sample": 10},
    "R2": {"threshold": 0.4, "min_sample": 10},
    "R3": {"threshold": 0.2, "min_sample": 10},
    "R4": {"threshold": 0.15, "min_sample": 10},
    "R5": {"threshold": 0.5, "min_sample": 5},
    "R6": {"threshold": 0.1, "min_sample": 10},
    "R7": {"threshold": 0.05, "min_sample": 10},
    "R8": {"threshold": 0.5, "low": 0.2, "min_sample": 5},
    "R9": {"threshold": 0.3, "min_sample": 10},
    "R10": {"threshold": 1.5, "min_sample": 10},
    "R11": {"threshold": 0.5, "min_sample": 5},
    "R12": {"threshold": 0.3, "min_sample": 10},
}

TEXTS = {
    "R1": "预算用尽的变更较多，且多数仍在进展，考虑提高 max_attempts",
    "R2": "实现阶段问题集中，先检查判据和合同是否清楚，再考虑默认档次",
    "R3": "初始档位经常在运行中升档，考虑调整该档位的分流规则",
    "R4": "规格返工较多，考虑改进合同模板或澄清过程",
    "R5": "代理失败较集中，考虑检查适配器稳定性",
    "R6": "轻档缺陷逃逸较多，考虑收紧轻档门槛",
    "R7": "本地通过后仍有持续集成失败，考虑加强本地判据",
    "R8": "强行继续后的失败或作废较多，考虑遵循拆分阈值",
    "R9": "审查发现被驳回较多，考虑复核审查阈值",
    "R10": "轻档的合同与实现改动比例偏高，考虑简化轻档流程",
    "R11": "费用集中在单一角色，考虑检查该角色的默认档次",
    "R12": "经验提供后仍有较多复发，考虑把相关条目升级为检查",
}

_R8_LOW_TEXT = "强行继续后的失败或作废较少，考虑放宽拆分阈值"
_R12_UNCLAIMED_TEXT = "经验提供后较少被声称使用，考虑收紧经验取用范围"
_UNRECORDED = "未记录"

MANUAL_DEFERRED = [
    "升级性质与黄金快照对拍",
    "隐式行为检测与技术栈 profile",
    "安全模块",
    "协作出口",
    "perf/docs/port 性质",
    "知识库补充",
    "跨仓库的经验晋升",
]


def thresholds(config: dict) -> dict:
    """Return defaults merged with validated per-project overrides."""
    limits = deepcopy(DEFAULTS)
    if not isinstance(config, dict):
        raise StoryLoopError("metrics 配置必须是对象")
    metrics = config.get("metrics", {})
    if not isinstance(metrics, dict):
        raise StoryLoopError("metrics 配置必须是对象")
    overrides = metrics.get("thresholds", {})
    if not isinstance(overrides, dict):
        raise StoryLoopError("metrics.thresholds 必须是对象")
    for rule, values in overrides.items():
        if rule not in limits:
            raise StoryLoopError(f"未知阈值键 {rule}")
        _merge_override(limits[rule], rule, values)
    return limits


def _merge_override(target: dict, rule: str, values: object) -> None:
    if not isinstance(values, dict):
        raise StoryLoopError(f"{rule} 必须是对象")
    allowed = set(target)
    for key, value in values.items():
        label = f"{rule}.{key}"
        if key not in allowed:
            raise StoryLoopError(f"未知阈值键 {label}")
        if key == "min_sample":
            if type(value) is not int or value < 0:
                raise StoryLoopError(f"{label} 必须是非负整数")
        elif not _number(value) or value < 0:
            raise StoryLoopError(f"{label} 必须是非负有限数值")
        target[key] = value


def suggest(metrics: dict, limits: dict) -> list[dict]:
    """Evaluate R1–R12 without turning missing evidence into negative conclusions."""
    if not isinstance(metrics, dict):
        return []
    evaluators = (
        _r1, _r2, _r3, _r4, _r5, _r6, _r7, _r8, _r9, _r10, _r11, _r12,
    )
    output: list[dict] = []
    for evaluator in evaluators:
        output.extend(evaluator(metrics, limits))
    return output


def _r1(metrics: dict, limits: dict) -> list[dict]:
    value = metrics.get("M1b")
    if not isinstance(value, dict):
        return []
    n = _integer(value.get("budget_denominator"), value.get("n"))
    exhausted = value.get("budget_exhausted")
    ratio = exhausted / n if _nonnegative(exhausted) and n and n > 0 else None
    pending = _precheck("R1", n, ratio, limits)
    if pending or ratio <= limits["R1"]["threshold"]:
        return pending
    progress = _metric_ratio(value, "progressing_value", "progressing",
                             "progressing_denominator")
    if progress is None:
        return [_unavailable("R1", "PROGRESS_EVIDENCE_NOT_RECORDED")]
    return [_fired("R1", ratio, limits["R1"]["threshold"], n,
                   value.get("changes"), TEXTS["R1"])] if progress > 0.5 else []


def _r2(metrics: dict, limits: dict) -> list[dict]:
    first, rework = metrics.get("M1"), metrics.get("M3")
    if not isinstance(first, dict) or not isinstance(rework, dict):
        return []
    n, value = _sample(first), _finite(first.get("value"))
    pending = _precheck("R2", n, value, limits)
    if pending or value >= limits["R2"]["threshold"]:
        return pending
    attribution = rework.get("attribution")
    if not isinstance(attribution, dict):
        return [_unavailable("R2", "ATTRIBUTION_EVIDENCE_NOT_RECORDED")]
    counts = [item for item in attribution.values() if _nonnegative(item)]
    total = sum(counts)
    if not total:
        return [_unavailable("R2", "ATTRIBUTION_EVIDENCE_NOT_RECORDED")]
    share = attribution.get("implement", 0) / total
    sources = rework.get("attribution_changes")
    evidence = sources.get("implement") if isinstance(sources, dict) else None
    if share > 0.6 and not _identifiers(evidence):
        return [_unavailable("R2", "ATTRIBUTION_EVIDENCE_NOT_RECORDED")]
    return [_fired("R2", value, limits["R2"]["threshold"], n,
                   evidence, TEXTS["R2"])] if share > 0.6 else []


def _r3(metrics: dict, limits: dict) -> list[dict]:
    value = metrics.get("M2")
    groups = value.get("by") if isinstance(value, dict) else None
    if not isinstance(groups, dict):
        return []
    output = []
    for name, row in sorted(groups.items()):
        result = _simple_high("R3", row, limits, group=name)
        for item in result:
            if "text" in item:
                item["rule_ids"] = _most_frequent(row.get("upgrade_rules"))
                if not item["rule_ids"]:
                    item["rules_unavailable"] = "UPGRADE_RULE_EVIDENCE_NOT_RECORDED"
        output.extend(result)
    return output


def _r4(metrics: dict, limits: dict) -> list[dict]:
    return _simple_high("R4", metrics.get("M3"), limits)


def _r5(metrics: dict, limits: dict) -> list[dict]:
    value = metrics.get("M4")
    if not isinstance(value, dict):
        return []
    n = _integer(value.get("agent_failure_denominator"), value.get("n"))
    average = _finite(value.get("agent_failure_value"))
    pending = _precheck("R5", n, average, limits)
    if pending:
        return pending
    if average > limits["R5"]["threshold"]:
        return [_fired("R5", average, limits["R5"]["threshold"], n,
                       value.get("agent_failure_changes"), TEXTS["R5"])]
    counts = value.get("agent_failures")
    if not isinstance(counts, dict):
        return [_unavailable("R5", "ADAPTER_EVIDENCE_NOT_RECORDED")]
    total = sum(item for item in counts.values() if _nonnegative(item))
    known = [(name, count) for name, count in counts.items()
             if name != _UNRECORDED and _nonnegative(count)]
    if not total:
        return []
    if not known and counts.get(_UNRECORDED, 0):
        return [_unavailable("R5", "ADAPTER_EVIDENCE_NOT_RECORDED")]
    adapter, count = max(known, key=lambda item: (item[1], item[0])) if known else (None, 0)
    share = count / total
    if share > 0.7:
        return [_fired("R5", share, 0.7, n, value.get("agent_failure_changes"),
                       TEXTS["R5"], group=adapter)]
    return ([_unavailable("R5", "ADAPTER_EVIDENCE_NOT_RECORDED")]
            if counts.get(_UNRECORDED, 0) else [])


def _r6(metrics: dict, limits: dict) -> list[dict]:
    value = metrics.get("M5")
    by = value.get("by") if isinstance(value, dict) else None
    return _simple_high("R6", by.get("light") if isinstance(by, dict) else None, limits)


def _r7(metrics: dict, limits: dict) -> list[dict]:
    value = metrics.get("M6")
    result = _simple_high("R7", value, limits)
    if not result or "text" not in result[0] or not isinstance(value, dict):
        return result
    layers = value.get("by")
    if not isinstance(layers, dict):
        return [_unavailable("R7", "CI_LAYER_EVIDENCE_NOT_RECORDED")]
    valid = [(name, count) for name, count in layers.items()
             if isinstance(name, str) and name and _nonnegative(count) and count > 0]
    if not valid:
        return [_unavailable("R7", "CI_LAYER_EVIDENCE_NOT_RECORDED")]
    result[0]["layers"] = [name for name, _ in sorted(
        valid, key=lambda item: (-item[1], item[0]))]
    return result


def _r8(metrics: dict, limits: dict) -> list[dict]:
    value = metrics.get("M7")
    if not isinstance(value, dict):
        return []
    n, ratio = _sample(value), _finite(value.get("value"))
    pending = _precheck("R8", n, ratio, limits)
    if pending:
        return pending
    evidence = value.get("failure_changes")
    if ratio > limits["R8"]["threshold"]:
        return [_fired("R8", ratio, limits["R8"]["threshold"], n,
                       evidence, TEXTS["R8"])]
    if ratio < limits["R8"]["low"]:
        return [_fired("R8", ratio, limits["R8"]["low"], n,
                       value.get("changes"), _R8_LOW_TEXT, direction="low")]
    return []


def _r9(metrics: dict, limits: dict) -> list[dict]:
    return _simple_high("R9", metrics.get("M8"), limits)


def _r10(metrics: dict, limits: dict) -> list[dict]:
    value = metrics.get("M9")
    by = value.get("by") if isinstance(value, dict) else None
    return _simple_high("R10", by.get("light") if isinstance(by, dict) else None, limits)


def _r11(metrics: dict, limits: dict) -> list[dict]:
    value = metrics.get("M11")
    if not isinstance(value, dict):
        return []
    calls = _integer(value.get("calls"), value.get("n"))
    coverage = _finite(value.get("cost_coverage"))
    if calls is None or coverage is None or not 0 <= coverage <= 1:
        return [_unavailable("R11", "COST_COVERAGE_NOT_RECORDED")]
    known = value.get("known_cost_calls")
    n = known if type(known) is int and 0 <= known <= calls else None
    if n is None:
        return [_unavailable("R11", "KNOWN_COST_CALLS_NOT_RECORDED")]
    pending = _precheck("R11", n, 0.0, limits)
    if pending:
        return pending
    roles, tiers = _role_costs(value.get("by"))
    total = sum(roles.values())
    if total <= 0:
        return []
    role, amount = max(roles.items(), key=lambda item: (item[1], item[0]))
    share = amount / total
    if share <= limits["R11"]["threshold"]:
        return []
    return [_fired("R11", share, limits["R11"]["threshold"], n,
                   value.get("changes"), TEXTS["R11"], group=role,
                   tiers=sorted(tiers[role]))]


def _r12(metrics: dict, limits: dict) -> list[dict]:
    value = metrics.get("M12")
    if not isinstance(value, dict):
        return []
    output = _simple_high("R12", value.get("recurrence_rate"), limits,
                          metric="recurrence_rate")
    unclaimed = value.get("unclaimed_rate")
    if not isinstance(unclaimed, dict):
        return output
    n, ratio = _sample(unclaimed), _finite(unclaimed.get("value"))
    pending = _precheck("R12", n, ratio, limits)
    if pending:
        return output + [{**pending[0], "metric": "unclaimed_rate"}]
    if ratio > 0.7:
        output.append(_fired("R12", ratio, 0.7, n, unclaimed.get("changes"),
                             _R12_UNCLAIMED_TEXT, metric="unclaimed_rate"))
    return output


def _simple_high(rule: str, value: object, limits: dict, **extra: object) -> list[dict]:
    if not isinstance(value, dict):
        return []
    n, ratio = _sample(value), _finite(value.get("value"))
    pending = _precheck(rule, n, ratio, limits)
    if pending:
        return [{**pending[0], **extra}]
    if ratio <= limits[rule]["threshold"]:
        return []
    return [_fired(rule, ratio, limits[rule]["threshold"], n,
                   value.get("changes"), TEXTS[rule], **extra)]


def _precheck(rule: str, n: int | None, value: float | None, limits: dict) -> list[dict]:
    if n is None or value is None:
        return [_unavailable(rule, "METRIC_EVIDENCE_NOT_RECORDED")]
    minimum = limits[rule]["min_sample"]
    return [{"rule": rule, "insufficient": f"{n}/{minimum}"}] if n < minimum else []


def _fired(rule: str, value: float, threshold: float, n: int, evidence: object,
           text: str, **extra: object) -> dict:
    identifiers = _identifiers(evidence)
    result = {"rule": rule, "value": round(value, 4), "threshold": threshold, "n": n,
              "evidence": identifiers[:10], "more": max(0, len(identifiers) - 10),
              "text": text}
    result.update(extra)
    return result


def _unavailable(rule: str, reason: str) -> dict:
    return {"rule": rule, "unavailable": reason}


def _role_costs(value: object) -> tuple[dict[str, float], dict[str, set[str]]]:
    roles: dict[str, float] = {}
    tiers: dict[str, set[str]] = {}
    if not isinstance(value, dict):
        return roles, tiers
    for key, row in value.items():
        if not isinstance(key, str) or "|" not in key or not isinstance(row, dict):
            continue
        role, tier = key.split("|", 1)
        amount = row.get("cost_usd")
        if not role or not _nonnegative(amount):
            continue
        roles[role] = roles.get(role, 0.0) + amount
        tiers.setdefault(role, set()).add(tier)
    return roles, tiers


def _most_frequent(value: object) -> list[str]:
    if not isinstance(value, dict):
        return []
    counts = {key: count for key, count in value.items()
              if isinstance(key, str) and key and _nonnegative(count)}
    highest = max(counts.values(), default=0)
    return sorted(key for key, count in counts.items() if count == highest and count > 0)


def deferred_signals(records: list[dict]) -> list[dict]:
    """Return four evidence-bound signals without reconstructing absent history."""
    chain = _longest_supersede_chain(records)
    smoke = _smoke_signal(records)
    unavailable = "HISTORICAL_EVIDENCE_NOT_RECORDED"
    return [
        {"item": "变更链执行语义", "triggered": len(chain) > 3,
         "evidence": chain if len(chain) > 3 else []},
        {"item": "故事级检查点", "triggered": False, "evidence": [],
         "unavailable": unavailable},
        smoke,
        {"item": "非确定性判据重复执行", "triggered": False, "evidence": [],
         "unavailable": unavailable},
    ]


def _longest_supersede_chain(records: list[dict]) -> list[str]:
    edges: dict[str, set[str]] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        change_id = record.get("change_id")
        if not isinstance(change_id, str):
            continue
        older, newer = record.get("supersedes"), record.get("superseded_by")
        if isinstance(older, str):
            edges.setdefault(older, set()).add(change_id)
        if isinstance(newer, str):
            edges.setdefault(change_id, set()).add(newer)
    candidates = [_walk_chain(node, edges, set()) for node in sorted(
        set(edges) | {target for targets in edges.values() for target in targets})]
    return max(candidates, key=lambda chain: (len(chain), tuple(chain)), default=[])


def _walk_chain(node: str, edges: dict[str, set[str]], seen: set[str]) -> list[str]:
    if node in seen:
        return []
    next_seen = seen | {node}
    tails = [_walk_chain(target, edges, next_seen) for target in sorted(edges.get(node, set()))]
    return [node] + max(tails, key=lambda chain: (len(chain), tuple(chain)), default=[])


def _smoke_signal(records: list[dict]) -> dict:
    passed = [record for record in records
              if isinstance(record, dict) and record.get("status") == "PASSED"][-10:]
    item = "顺利完成的变更也沉淀系统冒烟"
    if len(passed) < 10:
        return {"item": item, "triggered": False, "evidence": [],
                "unavailable": f"SAMPLE_INSUFFICIENT:{len(passed)}/10"}
    counts = [_smoke_count(record) for record in passed]
    if any(value is None for value in counts):
        return {"item": item, "triggered": False, "evidence": [],
                "unavailable": "SMOKE_HISTORY_NOT_RECORDED"}
    triggered = max(counts[1:]) <= counts[0]
    evidence = [record["change_id"] for record in passed if isinstance(record.get("change_id"), str)]
    return {"item": item, "triggered": triggered, "evidence": evidence if triggered else []}


def _smoke_count(record: dict) -> int | None:
    evidence = record.get("signal_evidence")
    layers = evidence.get("layers") if isinstance(evidence, dict) else None
    smoke = layers.get("smoke") if isinstance(layers, dict) else None
    results = smoke.get("results") if isinstance(smoke, dict) else None
    return len(results) if isinstance(results, list) else None


def _metric_ratio(value: dict, ratio_key: str, numerator_key: str,
                  denominator_key: str) -> float | None:
    ratio = _finite(value.get(ratio_key))
    if ratio is not None:
        return ratio
    numerator, denominator = value.get(numerator_key), value.get(denominator_key)
    return numerator / denominator if _nonnegative(numerator) and _nonnegative(denominator) and denominator else None


def _sample(value: dict) -> int | None:
    return _integer(value.get("n"), value.get("denominator"))


def _integer(*values: object) -> int | None:
    return next((value for value in values if type(value) is int and value >= 0), None)


def _finite(value: object) -> float | None:
    return value if _number(value) else None


def _number(value: object) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _nonnegative(value: object) -> bool:
    return _number(value) and value >= 0


def _identifiers(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(item for item in value if isinstance(item, str) and item))
