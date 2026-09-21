"""Deterministic requirement-quality checklist ("unit tests for requirements", spec-kit style).

Evaluated from the product contract alone: no agent call, same result every time. Errors block
approval; warnings are recorded in the change's checklist.md for the approver to weigh.
"""

from __future__ import annotations

import re

NEEDS_CLARIFICATION = re.compile(r"\[NEEDS CLARIFICATION[^\]]*\]", re.I)
VAGUE_WORDS = ("appropriate", "fast", "quickly", "user-friendly", "etc", "tbd", "todo", "as needed",
               "适当", "尽快", "合理", "等等", "待定", "友好", "快速")
EARS_MARKERS = ("SHALL", "MUST", "应", "必须", "须")


def evaluate_checklist(product: dict) -> list[dict]:
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
    return items


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
