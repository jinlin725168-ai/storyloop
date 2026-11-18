"""Event facts and conservative L1 intervention classification."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import math
import re

KINDS = frozenset({"contract_approval", "design_approval", "manual_acceptance", "answer",
                   "acceptance_repair", "replan", "overrule", "reset", "supersede",
                   "knowledge_approval", "manual_fix"})
# manual_fix: a red gate re-verified green with no agent call, i.e. a human edited the files in place.
CORRECTIONS = frozenset({"replan", "overrule", "reset", "supersede", "manual_fix"})
# Planning history written by the engine itself; older states lack the "automatic" flag.
ENGINE_REPLAN_REASONS = frozenset({"L3 scope or risk expansion", "Base upgrade changed plan inputs",
                                   "Characterization changed binding"})


def append_event(state: dict, kind: str, *, source: str, stop: str | None = None,
                 at: str | None = None, criterion_id: str | None = None,
                 automatic: bool = False, repeated: bool = False,
                 follows: str | None = None, defect_category: str | None = None) -> None:
    """Attach a known successful action to the state saved by its command."""
    if kind not in KINDS:
        raise ValueError(f"Unknown intervention kind: {kind}")
    from datetime import timezone
    event = {"kind": kind, "at": at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "stop": stop, "change_id": state["change_id"], "source": source}
    if criterion_id is not None:
        event["criterion_id"] = criterion_id
    if automatic:
        event["automatic"] = True
    if repeated:
        event["repeated"] = True
    if follows is not None:
        event["follows"] = follows
    if defect_category is not None:
        event["defect_category"] = defect_category
    state.setdefault("intervention_events", []).append(event)


def summarize_interventions(records: list[dict]) -> dict:
    """Report observed or inferred human activity without replacing missing evidence with zero."""
    by_change = {record["change_id"]: _row(record) for record in records}
    known = list(by_change.values())
    totals = {key: sum(row[key] for row in known if row[key] is not None)
              for key in ("routine", "nonroutine", "unknown", "automatic_repair")}
    # Roadmap G core metric: commands a person typed, per change (from the touch ledger).
    counted = [row["touches"] for row in known if isinstance(row.get("touches"), int)]
    totals["touches"] = sum(counted) if counted else None
    totals["touches_per_change"] = round(sum(counted) / len(counted), 2) if counted else None
    totals["routine_types"] = _merge_types(known, "routine_types")
    totals["nonroutine_types"] = _merge_types(known, "nonroutine_types")
    totals["nonroutine_by_class"] = _merge_types(known, "nonroutine_by_class")
    totals["agent_ms"] = sum(row["agent_ms"] for row in known) if all(
        row["agent_ms"] is not None for row in known) else None
    costs = [row["known_cost_usd"] for row in known if row["known_cost_usd"] is not None]
    totals["known_cost_usd"] = round(sum(costs), 4) if costs else None
    calls = sum(row["cost_calls"] for row in known)
    covered = sum(row["known_cost_calls"] for row in known)
    totals["cost_coverage"] = round(covered / calls, 4) if calls else None
    totals["wall_ms"] = sum(row["wall_ms"] for row in known if row["wall_ms"] is not None) if all(
        row["wall_ms"] is not None for row in known) else None
    totals["human_wait_ms"] = sum(row["human_wait_ms"] for row in known if row["human_wait_ms"] is not None) if all(
        row["human_wait_ms"] is not None for row in known) else None
    return {"id": "L1", "by_change": by_change, "summary": totals}


def _row(record: dict) -> dict:
    events, source = _effective_events(record)
    if record.get("run_present") is False:
        source = "partial"
    routine, nonroutine, unknown, automatic_repair = _classify(events, record["change_id"])
    missing = events is None or record.get("run_present") is False
    usage = _usage(record)
    wait = [_duration(event.get("stopped_at"), event.get("at")) for event in events or []
            if isinstance(event, dict) and event.get("source") != "engine"]
    return {"source": source, "touches": (record.get("touches") or {}).get("total"),  # None: no ledger
            "routine": sum(routine.values()) if events is not None else None,
            "nonroutine": sum(nonroutine.values()) if not missing else None,
            "unknown": unknown + int(missing), "routine_types": dict(sorted(routine.items())),
            "nonroutine_types": dict(sorted(nonroutine.items())), "automatic_repair": automatic_repair,
            "nonroutine_by_class": _by_stop_class(events, record["change_id"]),
            "human_wait_ms": sum(wait) if wait and all(v is not None for v in wait) else None,
            "wall_ms": _duration(record.get("started_at"), record.get("ended_at")), **usage}


def _effective_events(record: dict) -> tuple[list[dict] | None, str]:
    events = record.get("intervention_events")
    source = "observed" if isinstance(events, list) else "inferred"
    if isinstance(events, list):
        legacy = _legacy_events(record) or []
        observed = Counter((item.get("kind"), item.get("automatic") is True) for item in events
                           if isinstance(item, dict))
        inferred = []
        for item in legacy:
            key = (item["kind"], item.get("automatic") is True)
            if observed[key]:
                observed[key] -= 1
            else:
                inferred.append(item)
        if inferred:
            events = [*events, *inferred]
            source = "mixed"
    else:
        events = _legacy_events(record)
    return events, source


def _classify(events: list[dict] | None, change_id: str) -> tuple[Counter, Counter, int, int]:
    routine, nonroutine = Counter(), Counter()
    unknown = automatic_repair = 0
    for event in events or []:
        category = _event_class(event, change_id)
        if category == "unknown":
            unknown += 1
        elif category == "automatic_repair":
            automatic_repair += 1
        elif category == "nonroutine":
            nonroutine[event["kind"]] += 1
        elif category == "routine":
            routine[event["kind"]] += 1
    return routine, nonroutine, unknown, automatic_repair


def _by_stop_class(events: list[dict] | None, change_id: str) -> dict:
    """Who a non-routine intervention answered: infrastructure, contract, oracle, ... (C3)."""
    counts = Counter(stop_class(str(event.get("stop"))) for event in events or []
                     if _event_class(event, change_id) == "nonroutine")
    return dict(sorted(counts.items()))


def _event_class(event: object, change_id: str) -> str:
    if not isinstance(event, dict) or event.get("kind") not in KINDS:
        return "unknown"
    if not event.get("inferred") and not _complete_event(event, change_id):
        return "unknown"
    kind = event["kind"]
    if kind == "acceptance_repair" and event.get("automatic") is True:
        return "automatic_repair"
    if kind == "replan" and event.get("automatic") is True:
        return "not_counted"
    if kind in CORRECTIONS or kind == "acceptance_repair" or event.get("repeated") is True:
        return "nonroutine"
    if kind == "answer" and event.get("planned") is not True:
        return "nonroutine" if event.get("planned") is False else "unknown"
    return "routine"


def intervention_view(record: dict) -> dict:
    """Expose the effective L1 events and exactly the L1 totals for one change."""
    from .processutil import redact

    events, _ = _effective_events(record)
    row = _row(record)
    observed = []
    inferred = []
    for event in events or []:
        item = event if isinstance(event, dict) else {}
        legacy = item.get("inferred") is True
        view = {"at": None if legacy else _safe_field(item.get("at"), redact),
                "kind": _safe_field(item.get("kind"), redact) or "",
                "source": _safe_field(item.get("source"), redact),
                "stop": _safe_field(item.get("stop"), redact),
                "class": _event_class(event, record["change_id"]), "inferred": legacy}
        (inferred if legacy else observed).append(view)
    observed.sort(key=lambda item: _event_time(item["at"]))
    return {"events": observed + inferred,
            "totals": {key: row[key] for key in
                       ("routine", "nonroutine", "automatic_repair", "unknown")}}


def _safe_field(value: object, redact) -> str | None:
    return redact(value) if isinstance(value, str) else None


def _event_time(value: str | None) -> tuple[bool, datetime, str]:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None
        if parsed is not None and parsed.tzinfo is not None:
            return False, parsed.astimezone(timezone.utc), value
    except ValueError:
        pass
    return True, datetime.max.replace(tzinfo=timezone.utc), value or ""


def _usage(record: dict) -> dict:
    ledger = record.get("ledger")
    if not isinstance(ledger, list) or record.get("ledger_present") is False:
        return {"agent_ms": None, "known_cost_usd": None, "cost_calls": 0,
                "known_cost_calls": 0, "cost_coverage": None}
    entries = ledger
    costs = [entry.get("cost_usd") for entry in entries if isinstance(entry, dict)]
    known_costs = [v for v in costs if type(v) in (float, int) and math.isfinite(v) and v >= 0]
    agent_ms = sum(entry.get("wall_ms", 0) for entry in entries if isinstance(entry, dict)
                   and type(entry.get("wall_ms")) in (int, float) and entry["wall_ms"] >= 0)
    return {"agent_ms": agent_ms,
            "known_cost_usd": round(sum(known_costs), 4), "cost_calls": len(costs),
            "known_cost_calls": len(known_costs),
            "cost_coverage": round(len(known_costs) / len(costs), 4) if costs else None}


def _legacy_events(record: dict) -> list[dict] | None:
    if record.get("run_present") is False:
        return None
    fields = (record.get("repair_entries"), record.get("overrules"), record.get("planning_history"))
    if all(value is None for value in fields) and record.get("status") != "PASSED":
        return None
    events = []
    if record.get("contract_approved") is True:
        events.append({"kind": "contract_approval"})
    if record.get("design_approved") is True:
        events.append({"kind": "design_approval"})
    for _ in range(_count(record.get("manual_acceptance"))):
        events.append({"kind": "manual_acceptance"})
    for _ in range(_count(record.get("resets"))):
        events.append({"kind": "reset"})
    if record.get("superseded_by"):
        events.append({"kind": "supersede"})
    for _ in range(_count(record.get("answers"))):
        events.append({"kind": "answer"})
    for field, kind in (("repair_entries", "acceptance_repair"), ("overrules", "overrule"),
                        ("planning_history", "replan")):
        value = record.get(field)
        if isinstance(value, list):
            events.extend({"kind": kind, "automatic": item.get("automatic") is True or (
                              kind == "replan" and item.get("reason") in ENGINE_REPLAN_REASONS)}
                          for item in value if isinstance(item, dict))
    return [{**event, "inferred": True} for event in events]


def _count(value: object) -> int:
    return value if type(value) is int and 0 <= value <= 10000 else 0


def _complete_event(event: dict, change_id: str) -> bool:
    return (event.get("change_id") == change_id and event.get("kind") in KINDS
            and isinstance(event.get("at"), str) and _duration(event["at"], event["at"]) == 0
            and isinstance(event.get("stop"), str) and bool(event["stop"])
            and isinstance(event.get("source"), str) and bool(event["source"]))


def _duration(start: object, end: object) -> int | None:
    try:
        if not isinstance(start, str) or not isinstance(end, str):
            return None
        a = datetime.fromisoformat(start.replace("Z", "+00:00"))
        b = datetime.fromisoformat(end.replace("Z", "+00:00"))
        return max(0, int((b - a).total_seconds() * 1000)) if a.tzinfo and b.tzinfo else None
    except ValueError:
        return None


def _merge_types(rows: list[dict], key: str) -> dict:
    counter = Counter()
    for row in rows:
        counter.update(row[key])
    return dict(sorted(counter.items()))


# Stop classes and agent-unavailability reasons (roadmap C1/C3). The class says who acts on a
# stop: infrastructure and configuration stops are fixed outside the change (login, quota,
# workspace, readiness); contract and oracle stops revise what is verified; implementation stops
# spend the attempt budget; human stops are planned checkpoints. L1 metrics and the B4
# accounting read the class instead of re-deriving it from each status.
STOP_CLASSES = {
    "infrastructure": ("AGENT_UNAVAILABLE", "AGENT_CONTRACT_INVALID", "COST_LIMIT_REACHED",
                       "RED_ENVIRONMENT_ERROR", "INTERRUPTED", "HARNESS_SHADOWED", "WORKSPACE_TAINTED"),
    "configuration": ("READINESS_APPROVAL_REQUIRED", "CI_PROTECTED", "BASE_CHANGE_REQUIRED",
                      "BASE_REVIEW_REQUIRED", "BASE_AGENT_REMOVED"),
    "contract": ("NEEDS_SPEC_FIX", "NEEDS_INPUT", "EXPLORATION_RECOMMENDED"),
    "oracle": ("ORACLE_DEFECT", "ORACLE_STRENGTH_PRECHECK", "CHARACTERIZATION_FAILED"),
    "implementation": ("BUDGET_EXHAUSTED", "NO_PROGRESS", "BOUNDARY_VIOLATION",
                       "DESIGN_REVISION_REQUIRED", "FAILED"),
    "human": ("DESIGN_APPROVAL_REQUIRED", "MANUAL_ACCEPTANCE_REQUIRED", "HUMAN_IMPLEMENTATION_REQUIRED"),
}
CLASS_LABELS = {"infrastructure": "基础设施", "configuration": "配置", "contract": "契约",
                "oracle": "判据质量", "implementation": "实现", "human": "需要人工", "other": "其他"}
_BY_STATUS = {status: name for name, members in STOP_CLASSES.items() for status in members}

# First match wins; quota before network because rate-limit messages often mention HTTP.
_REASONS = (
    ("missing", r"executable is unavailable|command not found|No such file or directory"),
    ("auth", r"authenticat|oauth|not logged in|/login|unauthori[sz]ed|\b401\b|invalid api key"),
    ("quota", r"usage limit|hit your [\w -]*limit|quota|rate.?limit|\b429\b|too many requests|credit|insufficient.balance"),
    ("network", r"ENOTFOUND|ECONNRE|ETIMEDOUT|getaddrinfo|network|connection (?:refused|reset)|\b50[234]\b"),
    ("timeout", r"timed_out|timed out|timeout"),
)
REASON_HINTS = {"auth": "代理登录已失效：重新登录代理（如 claude /login、codex login）后再 run",
                "quota": "代理额度或限流：等额度恢复后再 run，或配置备用适配器",
                "network": "网络不可达：恢复网络连接后再 run",
                "timeout": "代理超时：检查 agent_timeout_seconds 或任务规模后再 run",
                "missing": "找不到代理程序：检查 agent_command 或安装后再 run",
                "crash": "代理进程异常退出：查看调用日志 stderr 后再 run"}


def stop_class(status: str) -> str:
    return _BY_STATUS.get(status, "other")


def unavailable_reason(message: str) -> str:
    """Classify an AGENT_UNAVAILABLE message; `crash` when nothing more specific matches."""
    for reason, pattern in _REASONS:
        if re.search(pattern, message, re.IGNORECASE):
            return reason
    return "crash"


def classify(status: str, details: dict) -> dict:
    """The fields a stop report adds: its class and, for an unavailable agent, the reason."""
    fields = {"class": stop_class(status)}
    if status == "AGENT_UNAVAILABLE":
        fields["reason"] = details.get("reason") or unavailable_reason(str(details.get("error") or ""))
    return fields


def render_class(stop: dict) -> list[str]:
    """One line only where the class changes what to do next (an unavailable agent)."""
    hint = REASON_HINTS.get(stop.get("reason") or "")
    label = CLASS_LABELS.get(stop.get("class") or "", "")
    return [f"停点类别：{label}（{hint}）"] if hint and label else []
