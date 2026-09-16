"""Validation contracts for product-loop agent responses."""

from __future__ import annotations

from .models import require


def validate_agent_summary(value: object) -> None:
    require(isinstance(value, dict) and set(value) == {"summary", "questions"},
            "Implementation agent returned an invalid contract")
    require(isinstance(value["summary"], str), "Implementation summary must be text")
    require(isinstance(value["questions"], list)
            and all(isinstance(item, str) for item in value["questions"]),
            "Implementation questions must be a list")


def validate_review(value: object) -> str:
    require(isinstance(value, dict)
            and set(value) == {"verdict", "summary", "findings", "questions"},
            "Review agent returned an invalid contract")
    require(value["verdict"] in {"pass", "fail", "needs_input"},
            "Invalid review verdict")
    require(isinstance(value["summary"], str) and isinstance(value["findings"], list)
            and isinstance(value["questions"], list), "Invalid review details")
    if value["verdict"] == "pass":
        require(not value["findings"] and not value["questions"],
                "Review cannot pass with findings or questions")
    return value["verdict"]


def require_fully_automated(change: dict) -> None:
    manual = [criterion["id"] for story in change["product"]["stories"]
              for criterion in story["acceptance"] if criterion["kind"] == "manual"]
    require(not manual, "Manual acceptance criteria require a separate human acceptance step")
