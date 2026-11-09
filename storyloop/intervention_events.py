"""Event facts and conservative L1 intervention classification."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
import math

KINDS = frozenset({"contract_approval", "design_approval", "manual_acceptance", "answer",
                   "acceptance_repair", "replan", "overrule", "reset", "supersede",
                   "knowledge_approval"})
CORRECTIONS = frozenset({"replan", "overrule", "reset", "supersede"})


def append_event(state: dict, kind: str, *, source: str, stop: str | None = None,
                 at: str | None = None, criterion_id: str | None = None,
                 automatic: bool = False, repeated: bool = False,
                 follows: str | None = None) -> None:
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
    state.setdefault("intervention_events", []).append(event)


def summarize_interventions(records: list[dict]) -> dict:
    """Report observed or inferred human activity without replacing missing evidence with zero."""
    by_change = {record["change_id"]: _row(record) for record in records}
    known = list(by_change.values())
    totals = {key: sum(row[key] for row in known if row[key] is not None)
              for key in ("routine", "nonroutine", "unknown", "automatic_repair")}
    totals["routine_types"] = _merge_types(known, "routine_types")
    totals["nonroutine_types"] = _merge_types(known, "nonroutine_types")
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
    return {"source": source, "routine": sum(routine.values()) if events is not None else None,
            "nonroutine": sum(nonroutine.values()) if not missing else None,
            "unknown": unknown + int(missing), "routine_types": dict(sorted(routine.items())),
            "nonroutine_types": dict(sorted(nonroutine.items())), "automatic_repair": automatic_repair,
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
        if not isinstance(event, dict) or event.get("kind") not in KINDS:
            unknown += 1
            continue
        if not event.get("inferred") and not _complete_event(event, change_id):
            unknown += 1
            continue
        kind = event["kind"]
        if kind == "acceptance_repair" and event.get("automatic") is True:
            automatic_repair += 1
        elif kind in CORRECTIONS or kind == "acceptance_repair" or event.get("repeated") is True:
            nonroutine[kind] += 1
        elif kind == "answer" and event.get("planned") is not True:
            if event.get("planned") is False:
                nonroutine[kind] += 1
            else:
                unknown += 1
        else:
            routine[kind] += 1
    return routine, nonroutine, unknown, automatic_repair


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
            events.extend({"kind": kind, "automatic": item.get("automatic") is True}
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
