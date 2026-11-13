"""Frozen, interleaved bench scheduling and evidence based comparison."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import uuid

from .bench_case import load_case
from .intervention_events import CORRECTIONS, KINDS
from .models import StoryLoopError, require

SCORE_CONTRACT = {"version": 3, "pass_requires": ["PASSED", "gold_passed"],
                  "attempts_per_side": 2, "nonroutine_policy": "bounded_recovery",
                  "nonroutine_budget": 1,
                  "missing_evidence": "NOT_COMPARABLE",
                  "cost_policy": "report_separately"}


def estimate_duration(case_count: int, scope: str) -> str:
    """Return cumulative 40–65 minute per attempt estimate."""
    attempts = case_count * 2 * (1 if scope == "base-only" else 2)
    return f"约 {attempts * 40 / 60:.1f}～{attempts * 65 / 60:.1f} 小时"


def run_bench(sources: list[Path], base_engine_commit: str, candidate_engine_commit: str,
              adapter_name: str, model: str, budget: dict, invoke, check_gold,
              output_root: Path, *, i_accept_cost: bool = False,
              gold_python: Path | None = None, scope: str = "paired",
              confirmed: bool = False, suite_source: str = "generated") -> dict:
    """Run one frozen comparison; invoke/check_gold are explicit runtime adapters."""
    if adapter_name != "test" and not i_accept_cost:
        attempts = len(sources) * 2 * (1 if scope == "base-only" else 2)
        raise StoryLoopError(f"Real bench runs require --i-accept-cost; "
                             f"estimated {attempts} 次，{estimate_duration(len(sources), scope)}")
    if adapter_name != "test" and not confirmed:
        raise StoryLoopError("Real bench runs require a second confirmation")
    require(adapter_name in {"test", "codex", "claude"}, "Unsupported bench adapter")
    require(scope in {"paired", "base-only"}, "Invalid bench execution scope")
    require(suite_source in {"generated", "frozen"}, "Invalid bench suite source")
    require(len(base_engine_commit) == 40 and
            (scope == "base-only" or len(candidate_engine_commit) == 40),
            "Engine commits must be frozen full hashes")
    require(isinstance(budget, dict) and type(budget.get("max_calls")) is int
            and budget["max_calls"] > 0, "Invalid bench budget")
    cases = [load_case(path) for path in sources]
    require(len({case["manifest"]["case_id"] for case in cases}) == len(cases),
            "Run Case IDs must be unique")
    if check_gold is None:
        from .bench_calibrate import _python_info, judge_gold
        interpreter = _python_info(gold_python, cases[0]["manifest"]["python"])

        def check_gold(case, outcome):
            if isinstance(outcome.get("gold_passed"), bool):
                return outcome["gold_passed"]
            require(isinstance(outcome.get("tree"), str), "Bench attempt lacks a candidate tree")
            return judge_gold(case["source"], Path(outcome["tree"]),
                              python=Path(interpreter["path"]))["passed"]
    else:
        interpreter = None
    for case in cases:
        allowed = case["manifest"]["allowed"]
        require(adapter_name in allowed["adapters"] and
                (model in allowed["models"] or "configured" in allowed["models"]),
                "Case does not allow this adapter/model")
        require(budget["max_calls"] <= allowed["max_calls"], "Budget exceeds Case policy")
    versions = {case["manifest"]["case_version"] for case in cases}
    require(len(versions) == 1, "v1 and v2 Cases cannot be mixed")
    revisions = {case["manifest"].get("case_revision") for case in cases}
    require(len(revisions) == 1, "Case revisions cannot be mixed")
    result = {"run_id": uuid.uuid4().hex, "created_at": datetime.now(timezone.utc).isoformat(),
              "case_version": versions.pop(), "case_revision": revisions.pop(),
              "scope": scope, "suite_source": suite_source,
              "base_engine_commit": base_engine_commit,
              "candidate_engine_commit": candidate_engine_commit, "adapter": adapter_name,
              "model": model, "budget": budget, "gold_python": interpreter,
              "scoring_contract": SCORE_CONTRACT, "cases": []}
    for case in cases:
        row = {"case_id": case["manifest"]["case_id"], "case_version": case["manifest"]["case_version"],
               "case_revision": case["manifest"].get("case_revision"),
               "suite_source": suite_source,
               "case_hash": case["case_hash"], "contract_sha256": case["manifest"]["contract_sha256"],
               "suite_sha256": case["manifest"]["suite_sha256"],
               "gold_sha256": case["gold_hash"], "attempts": []}
        for attempt in (1, 2):
            for side in (("base",) if scope == "base-only" else ("base", "candidate")):
                outcome = invoke(side, case, attempt, simulate_human)
                row["attempts"].append(_attempt(outcome, side, attempt, case, check_gold))
        comparison = _comparison(row["attempts"])
        if scope == "base-only" and comparison["status"] == "COMPARABLE":
            comparison = {**comparison, "status": "BASE_ONLY"}
        row["comparison"] = comparison
        result["cases"].append(row)
    if result["case_version"] == 2 and scope == "base-only" and suite_source == "generated":
        from .bench_real_exec import STAGE_CASE_IDS, stage_gate
        if {row["case_id"] for row in result["cases"]} == STAGE_CASE_IDS:
            result["stage_gate"] = stage_gate(result)
    records = Path(output_root) / ".storyloop/bench/runs"
    records.mkdir(parents=True, exist_ok=True)
    target = records / f"{result['run_id']}.json"
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    result["record_path"] = str(target)
    return result


def _attempt(outcome: dict, side: str, number: int, case: dict, check_gold) -> dict:
    require(isinstance(outcome, dict), "Bench adapter returned no outcome")
    events = outcome.get("events")
    routine, nonroutine, unknown = _events(events)
    stop = outcome.get("nonroutine_stop")
    if isinstance(stop, str) and not nonroutine:
        nonroutine[stop] += 1
    status = outcome.get("status")
    if status == "NEEDS_HUMAN" or sum(nonroutine.values()) > SCORE_CONTRACT["nonroutine_budget"]:
        verdict = "NEEDS_HUMAN"
        gold = None
    elif status == "PASSED":
        gold = check_gold(case, outcome)
        verdict = "PASSED" if gold is True else "FAILED" if gold is False else "NOT_COMPARABLE"
    elif status in {"NOT_COMPARABLE", "TIMEOUT", "BUDGET_EXHAUSTED", "COST_LIMIT_REACHED"}:
        gold = None
        verdict = "NOT_COMPARABLE"
    else:
        gold = None
        verdict = "NOT_COMPARABLE" if status is None else "FAILED"
    return {"side": side, "attempt": number, "status": status, "verdict": verdict,
            "gold_passed": gold, "gold_checks": outcome.get("gold_checks"),
            "engine_commit": outcome.get("engine_commit"),
            "engine_version": outcome.get("engine_version"),
            "engine_python": outcome.get("engine_python"),
            "attempt_python": outcome.get("attempt_python"),
            "readiness": outcome.get("readiness"),
            "readiness_evidence_path": outcome.get("readiness_evidence_path"),
            "stops": outcome.get("stops"),
            "stop_evidence_path": outcome.get("stop_evidence_path"),
            "termination_reason": outcome.get("termination_reason") or outcome.get("reason")
            or (status if status in {"BUDGET_EXHAUSTED", "COST_LIMIT_REACHED", "TIMEOUT"} else None),
            "routine_types": dict(routine),
            "nonroutine_types": dict(nonroutine), "nonroutine_count": sum(nonroutine.values()),
            "unknown_events": unknown, "calls": outcome.get("calls"),
            "input_tokens": outcome.get("input_tokens"),
            "output_tokens": outcome.get("output_tokens"),
            "agent_ms": outcome.get("agent_ms"), "wall_ms": outcome.get("wall_ms"),
            "known_cost_usd": outcome.get("known_cost_usd"),
            "cost_coverage": outcome.get("cost_coverage"),
            "cost_accounting": _cost_accounting(outcome)}


def _cost_accounting(outcome: dict) -> str:
    coverage = outcome.get("cost_coverage")
    if coverage is None or outcome.get("known_cost_usd") is None:
        return "PARTIAL" if isinstance(coverage, (int, float)) and coverage > 0 else "UNAVAILABLE"
    return "COMPLETE" if coverage >= 1 else "PARTIAL"


def simulate_human(event: dict) -> str:
    """Auto-approve known routine stops and stop at the first corrective decision."""
    routine, nonroutine, unknown = _events([event])
    if nonroutine or unknown or not routine:
        return "NEEDS_HUMAN"
    return "APPROVE"


def _events(events: object) -> tuple[Counter, Counter, int]:
    if not isinstance(events, list):
        return Counter(), Counter(), 1
    routine, nonroutine = Counter(), Counter()
    unknown = 0
    for event in events:
        if not isinstance(event, dict) or event.get("kind") not in KINDS:
            unknown += 1
            continue
        kind = event["kind"]
        if kind == "acceptance_repair" and event.get("automatic") is True:
            continue
        if kind in CORRECTIONS or kind == "acceptance_repair" or event.get("repeated"):
            nonroutine[kind] += 1
        elif kind == "answer" and event.get("planned") is not True:
            if event.get("planned") is False:
                nonroutine[kind] += 1
            else:
                unknown += 1
        else:
            routine[kind] += 1
    return routine, nonroutine, unknown


def _comparison(attempts: list[dict]) -> dict:
    reasons = []
    for field in ("calls", "agent_ms", "wall_ms"):
        if any(item[field] is None for item in attempts):
            reasons.append(field)
    if any(item["unknown_events"] or item["verdict"] == "NOT_COMPARABLE" for item in attempts):
        reasons.append("incomplete evidence")
    cost = "COMPARABLE" if all(item["cost_accounting"] == "COMPLETE" for item in attempts) else "NOT_COMPARABLE"
    if reasons:
        return {"status": "NOT_COMPARABLE", "reasons": sorted(set(reasons)), "cost_dimension": cost}
    return {"status": "COMPARABLE", "reasons": [],
            "cost_dimension": cost,
            "base": [item["verdict"] for item in attempts if item["side"] == "base"],
            "candidate": [item["verdict"] for item in attempts if item["side"] == "candidate"]}


def compare_runs(base: dict, candidate: dict) -> dict:
    """Reject comparisons across versions, frozen inputs, or execution settings."""
    fields = ("case_version", "case_revision", "scope", "suite_source", "adapter", "model", "budget", "gold_python",
              "scoring_contract")
    reasons = [field for field in fields if base.get(field) != candidate.get(field)]
    old = [(row.get("case_id"), row.get("case_hash")) for row in base.get("cases", [])]
    new = [(row.get("case_id"), row.get("case_hash")) for row in candidate.get("cases", [])]
    if old != new:
        reasons.append("case_hash")
    return {"status": "NOT_COMPARABLE" if reasons else "COMPARABLE", "reasons": reasons}
