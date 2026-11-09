"""Validation contracts for product-loop agent responses."""

from __future__ import annotations

from .models import require
from .role_schemas import validate_finding, validate_finding_ids
from .routing_risks import validate_routing_risks


def validate_agent_summary(value: object) -> None:
    require(isinstance(value, dict) and {"summary", "questions"} <= set(value)
            and set(value) <= {"summary", "questions", "design", "facts", "routing_risks", "lessons_used", "errata"},
            "Implementation agent returned an invalid contract")
    validate_routing_risks(value.get("routing_risks"))
    require(isinstance(value["summary"], str), "Implementation summary must be text")
    require(isinstance(value.get("design", ""), str), "Implementation design must be markdown text")
    require(isinstance(value["questions"], list)
            and all(isinstance(item, str) for item in value["questions"]),
            "Implementation questions must be a list")


def validate_review(value: object) -> str:
    require(isinstance(value, dict)
            and {"verdict", "summary", "findings", "questions"} <= set(value)
            and set(value) <= {"verdict", "summary", "findings", "questions", "facts", "routing_risks", "coverage", "checklist", "lessons_used", "errata"},
            "Review agent returned an invalid contract")
    require(value["verdict"] in {"pass", "fail", "needs_input"},
            "Invalid review verdict")
    require(isinstance(value["summary"], str) and isinstance(value["findings"], list)
            and isinstance(value["questions"], list), "Invalid review details")
    for item in value["findings"]:
        require(isinstance(item, dict), "Invalid review finding")
        validate_finding(item)
    validate_finding_ids(value["findings"])
    validate_routing_risks(value.get("routing_risks"))
    classified = any(item.get("disposition") is not None for item in value["findings"])
    if classified:
        if any(item.get("disposition") == "DECISION_NEEDED" for item in value["findings"]):
            return "needs_input"
        if any(item.get("disposition") is None for item in value["findings"]):
            return value["verdict"]
        if any(item.get("disposition") == "MUST_FIX" for item in value["findings"]):
            return "fail"
        return "needs_input" if value["questions"] else "pass"
    if value["verdict"] == "pass" and "coverage" not in value and not any(
            "confidence" in item for item in value["findings"]):
        require(not value["findings"] and not value["questions"],
                "Review cannot pass with findings or questions")
    return value["verdict"]
