"""Read-only project final Judge orchestration."""

from __future__ import annotations

import re
import shutil
from collections.abc import Callable, Mapping
from pathlib import Path

from .deadline import Clock, ensure_time_remaining
from .models import StoryLoopError, fields, identifier, require
from .stage_config import stage_order
from .stage_judges import run_stage_judge


SAFE_FINAL_FIELDS = (
    "request_id", "stage", "judge", "phase", "status", "outcome", "exit_code",
    "contained", "command_digest", "before_digest", "after_digest",
)
DIGEST = re.compile(r"[0-9a-f]{64}")


def safe_final_evidence(evidence: list[dict], config: dict,
                        completed_stages: list[str]) -> list[dict]:
    """Validate exact configured final evidence and remove narrative output."""
    expected = [(stage["id"], judge["id"]) for stage in stage_order(config)
                if stage["id"] in completed_stages
                for judge in stage["judges"] if judge["phase"] == "final"]
    require(isinstance(evidence, list) and len(evidence) == len(expected),
            "Final Judge evidence does not match configuration")
    safe = []
    for item, identity in zip(evidence, expected):
        require(isinstance(item, dict), "Invalid final Judge evidence")
        require(set(SAFE_FINAL_FIELDS) <= item.keys(), "Incomplete final Judge evidence")
        require((item["stage"], item["judge"]) == identity,
                "Final Judge evidence does not match configuration")
        identifier(item["judge"], "final judge")
        require(item["phase"] == "final" and item["status"] == "pass"
                and item["outcome"] == "GREEN" and item["exit_code"] == 0,
                "Invalid final Judge verdict")
        require(item["contained"] is True, "Final Judge was not contained")
        for name in ("request_id", "command_digest", "before_digest", "after_digest"):
            require(isinstance(item[name], str) and DIGEST.fullmatch(item[name]),
                    f"Invalid final Judge {name}")
        safe.append({name: item[name] for name in SAFE_FINAL_FIELDS})
    return safe

def run_final_judges(
    project: Path, config: dict, completed_stages: list[str], handoffs: list[dict],
    resolver: Callable[[str], str | None] = shutil.which,
    environ: Mapping[str, str] | None = None,
    attempt_deadline_ms: int | None = None,
    budget_clock_ms: Clock | None = None,
) -> list[dict]:
    """Run configured final Judges after the exact Stage set is complete."""
    ordered = stage_order(config)
    expected = [stage["id"] for stage in ordered if stage["id"] in completed_stages]
    require(completed_stages == expected and len(set(completed_stages)) == len(expected),
            "Completed Stage order is invalid")
    context = {"completed_stages": completed_stages, "handoffs": handoffs}
    evidence = []
    for stage in ordered:
        if stage["id"] not in completed_stages:
            continue
        for judge in (item for item in stage["judges"] if item["phase"] == "final"):
            ensure_time_remaining(attempt_deadline_ms, budget_clock_ms)
            result = run_stage_judge(
                project, config, stage["id"], judge["id"], context,
                resolver=resolver, environ=environ,
                attempt_deadline_ms=attempt_deadline_ms,
                budget_clock_ms=budget_clock_ms,
            )
            if result["outcome"] != "GREEN":
                code = {"FAIL": "JUDGE_FAILED", "ERROR": "JUDGE_ERROR",
                        "SKIP": "JUDGE_SKIPPED"}.get(result["outcome"], "JUDGE_BLOCKED")
                raise StoryLoopError("Final Judge did not pass", code=code,
                                     details={"evidence": result})
            evidence.append(result)
    return evidence
