"""Validation contracts for product-loop agent responses."""

from __future__ import annotations

from .models import require


def validate_agent_summary(value: object) -> None:
    require(isinstance(value, dict) and {"summary", "questions"} <= set(value)
            and set(value) <= {"summary", "questions", "design", "facts"},
            "Implementation agent returned an invalid contract")
    require(isinstance(value["summary"], str), "Implementation summary must be text")
    require(isinstance(value.get("design", ""), str), "Implementation design must be markdown text")
    require(isinstance(value["questions"], list)
            and all(isinstance(item, str) for item in value["questions"]),
            "Implementation questions must be a list")


def validate_review(value: object) -> str:
    require(isinstance(value, dict)
            and {"verdict", "summary", "findings", "questions"} <= set(value)
            and set(value) <= {"verdict", "summary", "findings", "questions", "facts"},
            "Review agent returned an invalid contract")
    require(value["verdict"] in {"pass", "fail", "needs_input"},
            "Invalid review verdict")
    require(isinstance(value["summary"], str) and isinstance(value["findings"], list)
            and isinstance(value["questions"], list), "Invalid review details")
    if value["verdict"] == "pass":
        require(not value["findings"] and not value["questions"],
                "Review cannot pass with findings or questions")
    return value["verdict"]
