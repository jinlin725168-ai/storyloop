"""Deterministic requirement-quality checklist ("unit tests for requirements", spec-kit style).

Evaluated from the product contract (plus, when a project is given, its locked base and settings):
no agent call, same result every time. Errors block approval; warnings are recorded in the change's
checklist.md for the approver to weigh. Contracts without a `nature` predate these checks and only
get warnings for them.
"""

from __future__ import annotations

import re
from pathlib import Path

from .change_types import definition_for
from .product_contract import nature_of
from .state_format import ROW_TYPES, criterion_expects_red

NEEDS_CLARIFICATION = re.compile(r"\[NEEDS CLARIFICATION[^\]]*\]", re.I)
VAGUE_WORDS = ("appropriate", "fast", "quickly", "user-friendly", "etc", "tbd", "todo", "as needed",
               "适当", "尽快", "合理", "等等", "待定", "友好", "快速")
EARS_MARKERS = ("SHALL", "MUST", "应", "必须", "须")


def evaluate_checklist(product: dict, project: Path | None = None) -> list[dict]:
    """Return checklist items: {id, dimension, level: ok|warning|error, text}."""
    items: list[dict] = []
    add = _adder(items)
    stories = product.get("stories") or []
    add("Completeness", "error" if not stories else "ok", "At least one user story is defined")
    add("Completeness", "warning" if not product.get("non_goals") else "ok", "Non-goals state what is out of scope")
    add("Clarity", "error" if product.get("questions") else "ok", "No open product questions remain")
    marked = [_find_marker(text) for text in _all_texts(product) if NEEDS_CLARIFICATION.search(text)]
    add("Clarity", "error" if marked else "ok",
        "No [NEEDS CLARIFICATION] markers remain" + (f" (found: {', '.join(marked[:3])})" if marked else ""))
    for story in stories:
        sid = story.get("id", "?")
        add("Completeness", "error" if not story.get("acceptance") else "ok",
            f"{sid} has acceptance criteria")
        add("Coverage", "warning" if len(story.get("acceptance") or []) < 2 else "ok",
            f"{sid} covers more than one scenario (primary plus alternate or failure)")
        add("Completeness", "warning" if not story.get("priority") else "ok", f"{sid} declares a priority")
        add("Measurability", "warning" if not story.get("independent_test") else "ok",
            f"{sid} says how it can be tested on its own")
        non_ears = [rule for rule in story.get("rules") or [] if not any(m in rule for m in EARS_MARKERS)]
        add("Clarity", "warning" if non_ears else "ok",
            f"{sid} rules are EARS obligations (SHALL/MUST)" + (f"; not: {non_ears[0][:60]}" if non_ears else ""))
        vague = [c["id"] for c in story.get("acceptance") or []
                 if any(word in (c.get("then") or "").lower() for word in VAGUE_WORDS)]
        add("Measurability", "warning" if vague else "ok",
            f"{sid} outcomes avoid vague words" + (f" (vague: {', '.join(vague)})" if vague else ""))
        manual = [c["id"] for c in story.get("acceptance") or [] if c.get("kind") == "manual"]
        automated = [c["id"] for c in story.get("acceptance") or [] if c.get("kind") == "automated"]
        add("Coverage", "warning" if manual and not automated else "ok",
            f"{sid} has at least one automated criterion" + (f" (only manual: {', '.join(manual)})" if manual and not automated else ""))
    _nature_items(add, product, project)
    return items


IDENTIFIER_LIKE = re.compile(r"\b[a-z]+[A-Z]\w*\b|\b\w+\(\)")


def _nature_items(add, product: dict, project: Path | None) -> None:
    """Checks that follow from the change's nature; legacy contracts (no nature) only get warnings."""
    nature = nature_of(product)
    floor = definition_for(nature).criteria_floor
    strict = "nature" in product
    criteria = [c for story in product.get("stories") or [] for c in story.get("acceptance") or []]
    automated = [c for c in criteria if c.get("kind") == "automated"]
    if "acceptance_red" in floor or "defect_reproduction_red" in floor:
        add("Coverage", "error" if strict and not any(criterion_expects_red(c) for c in automated) else "ok",
            "At least one automated criterion asserts new behaviour (must be red before implementation)")
        _row_coverage(add, product, criteria, "error" if strict else "warning")
    if "defect_reproduction_red" in floor:
        _fix_items(add, product, automated, project)
    if "structural_red" in floor:
        _refactor_items(add, product, automated, project)
    _consumer_items(add, product, criteria, "error" if strict else "warning")
    named = [c["id"] for c in criteria if IDENTIFIER_LIKE.search(" ".join(
        str(c.get(part, "")) for part in ("given", "when", "then")))]
    add("Clarity", "warning" if named else "ok",
        "Criteria describe observable behaviour, not class or function names"
        + (f" (check: {', '.join(named[:3])})" if named else ""))


def _row_coverage(add, product: dict, criteria: list, level: str) -> None:
    present = {c.get("row_type", "positive") for c in criteria}
    exempt = {item["row_type"] for item in product.get("coverage_exemptions") or []}
    for row in ROW_TYPES:
        add("Coverage", level if row not in present | exempt else "ok",
            f"Row type {row} has a criterion or a stated exemption")


def _fix_items(add, product: dict, automated: list, project: Path | None) -> None:
    defect = product["defect"]
    add("Coverage", "ok" if any(c.get("reproduces_defect") for c in automated) else "error",
        "An automated criterion reproduces the defect")
    for cause in defect["root_cause"]:
        found = project is None or _location_exists(project, cause["location"])
        add("Traceability", "ok" if found else "error",
            f"root cause location exists at the base: {cause['location']}")
    hypothesis = defect.get("reporter_hypothesis", "").strip()
    rules = " ".join(rule for story in product["stories"] for rule in story["rules"])
    add("Clarity", "warning" if hypothesis and hypothesis in rules else "ok",
        "The reporter's hypothesis is not copied into the rules as a requirement")


def _refactor_items(add, product: dict, automated: list, project: Path | None) -> None:
    goals = product["refactor"]["structural_goals"]
    red = [c for c in automated if criterion_expects_red(c)]
    add("Coverage", "ok" if len(red) >= len(goals) else "error",
        f"Every structural goal has an automated criterion ({len(red)} for {len(goals)} goals)")
    if project is not None:
        from .baseline import load_project_config  # local import keeps the checklist usable without a project
        profile = load_project_config(project)["profile"]
        configured = bool(profile.get("test_roots")) and bool(profile.get("unit_commands") or profile.get("test_commands"))
        add("Feasibility", "ok" if configured else "error",
            "A refactor's characterization gate is configured (test_roots and unit_commands)")


def _consumer_items(add, product: dict, criteria: list, level: str) -> None:
    covered = {consumer for c in criteria for consumer in c.get("consumers") or []}
    for consumer in product.get("consumers") or []:
        if consumer["in_scope"]:
            add("Coverage", level if consumer["id"] not in covered else "ok",
                f"In-scope consumer {consumer['id']} is covered by a criterion")


def _location_exists(project: Path, location: str) -> bool:
    """`path:line` names an existing line of a file at the locked base commit."""
    from .baseline import _git, load_baseline
    path, line = location.rsplit(":", 1)
    try:
        content = _git(project, "show", f"{load_baseline(project)['source_head']}:{path}")
    except Exception:  # noqa: BLE001  git reports a missing path through a non-zero exit
        return False
    return 1 <= int(line) <= max(len(content.splitlines()), 1)


def render_checklist(items: list[dict]) -> str:
    lines = ["# Requirement checklist", "",
             "Checked items passed automatically; unchecked items need the approver's judgement "
             "(warnings) or a contract revision (errors).", ""]
    for item in items:
        box = "[x]" if item["level"] == "ok" else "[ ]"
        tag = "" if item["level"] == "ok" else f" **{item['level'].upper()}**"
        lines.append(f"- {box} {item['id']} - {item['text']} [{item['dimension']}]{tag}")
    errors = sum(1 for item in items if item["level"] == "error")
    warnings = sum(1 for item in items if item["level"] == "warning")
    lines += ["", f"Summary: {len(items) - errors - warnings} passed, {warnings} warnings, {errors} blocking."]
    return "\n".join(lines) + "\n"


def blocking_items(items: list[dict]) -> list[dict]:
    return [item for item in items if item["level"] == "error"]


def _adder(items: list[dict]):
    def add(dimension: str, level: str, text: str) -> None:
        items.append({"id": f"CHK{len(items) + 1:03d}", "dimension": dimension, "level": level, "text": text})
    return add


def _all_texts(product: dict):
    yield product.get("goal") or ""
    for story in product.get("stories") or []:
        for key in ("title", "want", "so_that"):
            yield story.get(key) or ""
        yield from (story.get("rules") or [])
        for criterion in story.get("acceptance") or []:
            for key in ("given", "when", "then"):
                yield criterion.get(key) or ""


def _find_marker(text: str) -> str:
    match = NEEDS_CLARIFICATION.search(text)
    return match.group(0) if match else ""
