"""The product contract: validated stories, rules and acceptance criteria."""
from __future__ import annotations

import re


class StoryLoopError(RuntimeError):
    """A safe, actionable workflow failure with a machine-readable category."""

    def __init__(self, message: str, code: str = "BLOCKED",
                 details: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = {} if details is None else details


def require(condition: bool, message: str) -> None:
    if not condition:
        raise StoryLoopError(message)


def text(value: object, label: str) -> str:
    require(isinstance(value, str) and bool(value.strip()), f"{label} must be nonempty text")
    require(len(value) <= 20000, f"{label} is too long")
    return value


def identifier(value: object, label: str) -> str:
    text(value, label)
    require(bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", value)), f"Invalid {label}")
    return value


def strings(value: object, label: str) -> list:
    require(isinstance(value, list), f"{label} must be a list")
    for item in value:
        text(item, label)
    return value


def fields(value: object, required: set, optional: set | None = None) -> None:
    require(isinstance(value, dict), "Expected a JSON object")
    require(required <= value.keys(), f"Missing fields: {', '.join(sorted(required - value.keys()))}")
    require(value.keys() <= required | (optional or set()), "Unknown contract fields")


PRIORITIES = {"P1", "P2", "P3"}


def _questions(value: object) -> None:
    require(isinstance(value, list), "questions must be a list")
    seen = set()
    for item in value:
        fields(item, {"id", "text", "options"}, {"recommended", "why"})
        key = identifier(item["id"], "question id")
        require(key not in seen, "Duplicate question id")
        seen.add(key)
        text(item["text"], "question")
        strings(item["options"], "options")
        if "recommended" in item:
            require(item["recommended"] in item["options"],
                    "recommended must be one of the question's options")
        if "why" in item:
            text(item["why"], "why")


def _clarifications(value: object) -> None:
    require(isinstance(value, list), "clarifications must be a list")
    for item in value:
        fields(item, {"session", "question", "answer"})
        for key in ("session", "question", "answer"):
            text(item[key], key)


def _acceptance(value: object, seen: set) -> None:
    require(isinstance(value, list) and bool(value), "Story needs acceptance criteria")
    for criterion in value:
        fields(criterion, {"id", "given", "when", "then", "kind"})
        key = identifier(criterion["id"], "criterion id")
        require(key not in seen, "Duplicate acceptance id")
        seen.add(key)
        for part in ("given", "when", "then"):
            text(criterion[part], part)
        require(criterion["kind"] in {"automated", "manual"}, "Invalid acceptance kind")


def validate_product(value: object) -> dict:
    """Validate stories, business outcomes, global IDs and ordered dependencies."""
    fields(value, {"version", "goal", "non_goals", "questions", "stories"},
           {"capability", "clarifications"})
    require(value["version"] == 1, "Unsupported product version")
    text(value["goal"], "goal")
    strings(value["non_goals"], "non_goals")
    _questions(value["questions"])
    if "capability" in value:
        identifier(value["capability"], "capability")
    if "clarifications" in value:
        _clarifications(value["clarifications"])
    require(isinstance(value["stories"], list), "stories must be a list")
    seen, criteria = set(), set()
    for story in value["stories"]:
        _story(story, seen, criteria)
    return value


def _story(story: dict, seen: set, criteria: set) -> None:
    fields(story, {"id", "title", "as_a", "want", "so_that", "rules", "out_of_scope", "depends_on", "acceptance"},
           {"priority", "independent_test"})
    key = identifier(story["id"], "story id")
    if "priority" in story:
        require(story["priority"] in PRIORITIES, "priority must be P1, P2 or P3")
    if "independent_test" in story:
        text(story["independent_test"], "independent_test")
    require(key not in seen, "Duplicate story id")
    for field in ("title", "as_a", "want", "so_that"):
        text(story[field], field)
    for field in ("rules", "out_of_scope", "depends_on"):
        strings(story[field], field)
    require(set(story["depends_on"]) <= seen, "Dependencies must precede their story")
    require(len(set(story["depends_on"])) == len(story["depends_on"]), "Duplicate dependency")
    _acceptance(story["acceptance"], criteria)
    seen.add(key)
