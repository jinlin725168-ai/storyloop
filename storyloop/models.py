"""Validated product intent and executable acceptance contracts."""
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


def _check(value: dict, stories: dict, seen: set) -> None:
    fields(value, {"id", "story_id", "kind", "criteria", "command", "expected_tests", "timeout_seconds"})
    key = identifier(value["id"], "check id")
    require(key not in seen, "Duplicate check id")
    seen.add(key)
    require(value["story_id"] in stories, "Unknown check story")
    require(value["kind"] in {"health", "acceptance", "regression"}, "Invalid check kind")
    strings(value["command"], "command")
    require(bool(value["command"]) and len(value["command"]) <= 100, "command must contain argv")
    strings(value["criteria"], "criteria")
    strings(value["expected_tests"], "expected_tests")
    require(bool(value["expected_tests"]), "A check must execute named tests")
    require(len(set(value["expected_tests"])) == len(value["expected_tests"]), "Duplicate expected test")
    valid = {c["id"] for c in stories[value["story_id"]]["acceptance"] if c["kind"] == "automated"}
    require(set(value["criteria"]) <= valid, "Check references unknown or manual criterion")
    if value["kind"] == "acceptance":
        require(bool(value["criteria"]), "Acceptance check needs criterion coverage")
    require(type(value["timeout_seconds"]) is int and 1 <= value["timeout_seconds"] <= 3600, "Invalid check timeout")


def validate_checks(value: object, product: dict) -> dict:
    """Require executable coverage and a health check for every story."""
    fields(value, {"version", "checks"})
    require(value["version"] == 1 and isinstance(value["checks"], list), "Invalid checks document")
    stories = {s["id"]: s for s in product["stories"]}
    seen = set()
    for item in value["checks"]:
        _check(item, stories, seen)
    for key, story in stories.items():
        own = [c for c in value["checks"] if c["story_id"] == key]
        require(any(c["kind"] == "health" for c in own), f"{key} needs a health check")
        covered = {item for c in own if c["kind"] == "acceptance" for item in c["criteria"]}
        required = {c["id"] for c in story["acceptance"] if c["kind"] == "automated"}
        require(required <= covered, f"{key} has uncovered automated acceptance criteria")
    return value


def validate_config(value: object) -> dict:
    """Validate execution budgets and explicit adapter configuration."""
    names = {"version", "adapter", "agent_command", "max_attempts", "max_seconds",
             "agent_timeout_seconds", "check_timeout_seconds", "repeated_failure_limit"}
    fields(value, names)
    require(value["version"] == 1, "Unsupported config version")
    require(value["adapter"] in {"codex", "command", "manual"}, "Unknown adapter")
    strings(value["agent_command"], "agent_command")
    if value["adapter"] == "command":
        require(bool(value["agent_command"]), "Command adapter needs explicit argv")
    for field in names - {"version", "adapter", "agent_command"}:
        require(type(value[field]) is int and 0 < value[field] <= 86400, f"Invalid {field}")
    return value


def validate_review(value: object, story: dict) -> dict:
    """Reject contradictory or untraceable independent QA verdicts."""
    fields(value, {"verdict", "summary", "findings", "questions"})
    require(value["verdict"] in {"pass", "fail", "needs_input"}, "Unknown QA verdict")
    text(value["summary"], "QA summary")
    strings(value["questions"], "QA questions")
    require(isinstance(value["findings"], list), "QA findings must be a list")
    valid = {c["id"] for c in story["acceptance"]}
    for finding in value["findings"]:
        fields(finding, {"criterion_id", "message"})
        require(finding["criterion_id"] in valid, "QA finding references unknown criterion")
        text(finding["message"], "QA finding")
    if value["verdict"] == "pass":
        require(not value["findings"] and not value["questions"], "QA cannot pass with unresolved findings/questions")
    if value["verdict"] == "fail":
        require(bool(value["findings"]), "Failing QA must provide a traceable finding")
    if value["verdict"] == "needs_input":
        require(bool(value["questions"]), "QA needs_input must provide questions")
    return value
