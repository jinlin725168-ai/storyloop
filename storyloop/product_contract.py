"""The product contract: validated stories, rules and acceptance criteria."""
from __future__ import annotations
import re

from . import change_types
from .change_types import type_of
from .errors import require
from .models import fields, identifier, strings, text
from .path_security import repository_relative
from .state_format import LOCATION, ORACLE_CLASSES, ROW_TYPES, criterion_expects_red


PRIORITIES = {"P1", "P2", "P3"}
NATURES = tuple(change_types.CHANGE_TYPES)
SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,38}[a-z0-9])$")


def nature_of(product: dict) -> str:
    return type_of(product)


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


def _acceptance(value: object, seen: set, consumers: set) -> None:
    require(isinstance(value, list) and bool(value), "Story needs acceptance criteria")
    for criterion in value:
        fields(criterion, {"id", "given", "when", "then", "kind"},
               {"row_type", "oracle_class", "consumers", "reproduces_defect"})
        key = identifier(criterion["id"], "criterion id")
        require(key not in seen, "Duplicate acceptance id")
        seen.add(key)
        for part in ("given", "when", "then"):
            text(criterion[part], part)
        require(criterion["kind"] in {"automated", "manual"}, "Invalid acceptance kind")
        _criterion_extras(criterion, consumers)


def _criterion_extras(criterion: dict, consumers: set) -> None:
    require(criterion.get("row_type", "positive") in ROW_TYPES, "Invalid acceptance row_type")
    if "oracle_class" in criterion:
        require(criterion["oracle_class"] in ORACLE_CLASSES, "Invalid acceptance oracle_class")
        require((criterion["oracle_class"] == "manual") == (criterion["kind"] == "manual"),
                "oracle_class must be manual exactly when kind is manual")
    if "consumers" in criterion:
        strings(criterion["consumers"], "criterion consumers")
        require(set(criterion["consumers"]) <= consumers, "Criterion references an unknown consumer")
    if "reproduces_defect" in criterion:
        require(isinstance(criterion["reproduces_defect"], bool), "reproduces_defect must be a boolean")
        require(not criterion["reproduces_defect"] or criterion_expects_red(criterion),
                "A defect reproduction must be a must-be-red row")


def validate_product(value: object) -> dict:
    """Validate stories, business outcomes, global IDs, ordered dependencies and the nature blocks."""
    fields(value, {"version", "goal", "non_goals", "questions", "stories"},
           {"capability", "clarifications", "nature", "defect", "refactor", "base", "consumers",
            "coverage_exemptions", "test_removals", "assessment", "lessons_used", "errata", "slug"})
    require(value["version"] == 1, "Unsupported product version")
    text(value["goal"], "goal")
    strings(value["non_goals"], "non_goals")
    _questions(value["questions"])
    if "capability" in value:
        identifier(value["capability"], "capability")
    if "slug" in value:
        require(isinstance(value["slug"], str) and SLUG.fullmatch(value["slug"]) is not None,
                "Invalid feature slug")
    if "clarifications" in value:
        _clarifications(value["clarifications"])
    _nature_blocks(value)
    if nature_of(value) == "base":
        require(value["stories"] == [], "A base contract has no user stories")
        return value
    consumers = _consumers(value.get("consumers", []))
    _exemptions(value.get("coverage_exemptions", []))
    _removals(value.get("test_removals", []))
    require(isinstance(value["stories"], list), "stories must be a list")
    seen, criteria = set(), set()
    for story in value["stories"]:
        _story(story, seen, criteria, consumers)
    if "assessment" in value:
        from .assessment import validate_assessment
        validate_assessment(value["assessment"], value)
    return value


def _nature_blocks(value: dict) -> None:
    nature = value.get("nature", "feature")
    require(isinstance(nature, str) and nature in change_types.CHANGE_TYPES,
            "Invalid change nature")
    floor = change_types.definition_for(nature).criteria_floor
    defect = "defect_reproduction_red" in floor
    refactor = "structural_red" in floor
    require(("defect" in value) == defect, "A fix needs a defect block; other natures have none")
    require(("refactor" in value) == refactor,
            "A refactor needs a refactor block; other natures have none")
    require(("base" in value) == (nature == "base"),
            "A base change needs a base block; other natures have none")
    if nature == "base":
        _base(value["base"])
    if defect:
        _defect(value["defect"])
    if refactor:
        _refactor(value["refactor"])


def _base(value: object) -> None:
    fields(value, {"scope", "paths", "must_stay_green"})
    require(value["scope"] in {"foundation", "upstream", "ci", "skills", "knowledge"},
            "Invalid base scope")
    require(isinstance(value["paths"], list) and bool(value["paths"]),
            "Base contract needs paths")
    paths = [repository_relative(path, "base path") for path in value["paths"]]
    require(len(paths) == len(set(paths)), "Duplicate base path")
    require(value["must_stay_green"] == ["full_regression", "system_smoke", "build"],
            "Base contract must keep full regression, system smoke and build green")


def _defect(value: object) -> None:
    fields(value, {"observed", "expected", "reproduction", "root_cause", "confirmed", "unconfirmed"},
           {"reporter_hypothesis", "sibling_entries", "origin_change"})
    for key in ("observed", "expected"):
        text(value[key], key)
    require(bool(strings(value["reproduction"], "reproduction")), "reproduction needs steps")
    require(isinstance(value["root_cause"], list) and 1 <= len(value["root_cause"]) <= 5,
            "root_cause needs one to five statements")
    for cause in value["root_cause"]:
        fields(cause, {"statement", "location"})
        text(cause["statement"], "root cause statement")
        require(isinstance(cause["location"], str) and LOCATION.fullmatch(cause["location"]) is not None,
                "root cause location must be path:line")
    strings(value["confirmed"], "confirmed")
    strings(value["unconfirmed"], "unconfirmed")
    if "reporter_hypothesis" in value:
        text(value["reporter_hypothesis"], "reporter_hypothesis")
    if "sibling_entries" in value:
        strings(value["sibling_entries"], "sibling_entries")
    if "origin_change" in value:
        identifier(value["origin_change"], "origin change")


def _refactor(value: object) -> None:
    fields(value, {"subtype", "scope", "invariants", "structural_goals"}, {"migration_map"})
    require(value["subtype"] in {"structure", "dependency-swap"}, "Invalid refactor subtype")
    require(bool(strings(value["scope"], "refactor scope")), "refactor scope is empty")
    strings(value["invariants"], "invariants")
    require(bool(strings(value["structural_goals"], "structural_goals")),
            "A refactor needs at least one assertable structural goal")
    if value["subtype"] == "dependency-swap":
        require(isinstance(value.get("migration_map"), list) and bool(value["migration_map"]),
                "A dependency swap needs a migration map")
    for entry in value.get("migration_map", []):
        fields(entry, {"from", "to", "assertion"})
        for key in ("from", "to", "assertion"):
            text(entry[key], f"migration {key}")


def _consumers(value: object) -> set:
    require(isinstance(value, list), "consumers must be a list")
    seen: set = set()
    for item in value:
        fields(item, {"id", "consumer", "trigger", "path", "in_scope", "verification"})
        key = identifier(item["id"], "consumer id")
        require(key not in seen, "Duplicate consumer id")
        seen.add(key)
        for field in ("consumer", "trigger", "path", "verification"):
            text(item[field], field)
        require(isinstance(item["in_scope"], bool), "in_scope must be a boolean")
    return seen


def _exemptions(value: object) -> None:
    require(isinstance(value, list), "coverage_exemptions must be a list")
    rows = [item.get("row_type") for item in value if isinstance(item, dict)]
    require(len(rows) == len(set(rows)), "Duplicate coverage exemption")
    for item in value:
        fields(item, {"row_type", "reason"})
        require(item["row_type"] in ROW_TYPES, "Invalid exemption row_type")
        text(item["reason"], "exemption reason")


def _removals(value: object) -> None:
    require(isinstance(value, list), "test_removals must be a list")
    for item in value:
        fields(item, {"path", "reason"})
        text(item["path"], "removed test path")
        text(item["reason"], "removal reason")


def _story(story: dict, seen: set, criteria: set, consumers: set) -> None:
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
    _acceptance(story["acceptance"], criteria, consumers)
    seen.add(key)
