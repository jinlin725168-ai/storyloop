"""JSON Schemas for every role's final answer: one source for the engine check and the adapters.

The schemas are written in the strict subset both agent CLIs enforce natively (`codex exec
--output-schema`, `claude -p --json-schema`): every object closes `additionalProperties` and
lists every property as required. A field the engine treats as optional is declared nullable;
the engine drops null values before checking, so an adapter that simply omits the field still
passes. `x-open` marks objects the protocol leaves open (review findings): the engine accepts
extra keys there, the wire schema still asks for the declared ones only.
"""

from __future__ import annotations

import copy

MAX_ERRORS = 12

STRING = {"type": "string"}
STRINGS = {"type": "array", "items": STRING}


def _obj(properties: dict, open_: bool = False) -> dict:
    schema = {"type": "object", "properties": properties, "required": list(properties),
              "additionalProperties": False}
    if open_:
        schema["x-open"] = True
    return schema


def _nullable(schema: dict) -> dict:
    value = copy.deepcopy(schema)
    kind = value["type"]
    value["type"] = [*kind, "null"] if isinstance(kind, list) else [kind, "null"]
    if "enum" in value:
        value["enum"] = [*value["enum"], None]
    return value


FILES = {"type": "array", "items": _obj({"path": STRING, "content": STRING})}
ACCEPTANCE_TEST = _obj({"id": STRING, "criteria": STRINGS, "command": STRINGS,
                        "timeout_seconds": {"type": "integer"}})
SYSTEM_SMOKE_TEST = _obj({"id": STRING, "description": STRING, "command": STRINGS,
                          "timeout_seconds": {"type": "integer"}})
QUESTION = _obj({"id": STRING, "text": STRING, "options": STRINGS,
                 "recommended": _nullable(STRING), "why": _nullable(STRING)})
CRITERION = _obj({"id": STRING, "given": STRING, "when": STRING, "then": STRING,
                  "kind": {"type": "string", "enum": ["automated", "manual"]}})
STORY = _obj({
    "id": STRING, "title": STRING,
    "priority": _nullable({"type": "string", "enum": ["P1", "P2", "P3"]}),
    "independent_test": _nullable(STRING),
    "as_a": STRING, "want": STRING, "so_that": STRING, "rules": STRINGS,
    "out_of_scope": STRINGS, "depends_on": STRINGS,
    "acceptance": {"type": "array", "items": CRITERION},
})
FINDING = _obj({"message": STRING, "perspective": _nullable(STRING),
                "criterion_id": _nullable(STRING), "evidence": _nullable(STRING)}, open_=True)

SCHEMAS = {
    "product": _obj({
        "version": {"type": "integer", "enum": [1]}, "goal": STRING, "non_goals": STRINGS,
        "capability": _nullable(STRING),
        "clarifications": _nullable({"type": "array", "items": _obj(
            {"session": STRING, "question": STRING, "answer": STRING})}),
        "questions": {"type": "array", "items": QUESTION},
        "stories": {"type": "array", "items": STORY},
    }),
    "acceptance": _obj({"files": FILES, "manifest": _obj({
        "version": {"type": "integer", "enum": [1]},
        "tests": {"type": "array", "items": ACCEPTANCE_TEST}})}),
    "smoke-maintenance": _obj({"files": FILES, "manifest": _obj({
        "version": {"type": "integer", "enum": [1]},
        "tests": {"type": "array", "items": SYSTEM_SMOKE_TEST}}),
        "rationale": _nullable(STRING)}),
    "design": _obj({"summary": STRING, "touched": STRINGS, "design": STRING, "questions": STRINGS}),
    "characterize": _obj({"summary": STRING, "touched": STRINGS, "tests": STRINGS, "questions": STRINGS}),
    "implement": _obj({"summary": STRING, "questions": STRINGS, "design": _nullable(STRING)}),
    "review": _obj({"verdict": {"type": "string", "enum": ["pass", "fail", "needs_input"]},
                    "summary": STRING, "findings": {"type": "array", "items": FINDING},
                    "questions": STRINGS}),
    "survey": _obj({"documents": FILES}),
}


def schema_for(role: str, payload: dict) -> dict:
    """The engine-side schema of one call (acceptance changes shape in smoke-maintenance mode)."""
    if role == "acceptance" and payload.get("mode") == "smoke-maintenance":
        return SCHEMAS["smoke-maintenance"]
    return SCHEMAS[role]


def wire_schema(schema: dict) -> dict:
    """The schema handed to an agent CLI: engine-only markers removed."""
    if isinstance(schema, dict):
        return {key: wire_schema(value) for key, value in schema.items() if not key.startswith("x-")}
    if isinstance(schema, list):
        return [wire_schema(item) for item in schema]
    return schema


def normalize(value: object, schema: dict) -> object:
    """Drop null values of nullable properties, recursively, so optional fields read as absent."""
    if isinstance(value, dict) and "properties" in schema:
        result = {}
        for key, item in value.items():
            child = schema["properties"].get(key)
            if child is None:
                result[key] = item
            elif item is None and _allows_null(child):
                continue
            else:
                result[key] = normalize(item, child)
        return result
    if isinstance(value, list) and "items" in schema:
        return [normalize(item, schema["items"]) for item in value]
    return value


def schema_errors(value: object, schema: dict, path: str = "$") -> list[str]:
    """Structural mismatches as short `path: problem` lines (at most MAX_ERRORS)."""
    errors: list[str] = []
    _check(value, schema, path, errors)
    return errors[:MAX_ERRORS]


def _check(value: object, schema: dict, path: str, errors: list[str]) -> None:
    if len(errors) >= MAX_ERRORS:
        return
    if not _type_matches(value, schema["type"]):
        errors.append(f"{path}: expected {_type_name(schema['type'])}, got {_type_name(_json_type(value))}")
        return
    if "enum" in schema and value not in schema["enum"]:
        allowed = ", ".join(repr(item) for item in schema["enum"] if item is not None)
        errors.append(f"{path}: must be one of {allowed}")
        return
    if isinstance(value, dict) and "properties" in schema:
        properties = schema["properties"]
        for key in schema.get("required", []):
            if key not in value and not _allows_null(properties[key]):
                errors.append(f"{path}: missing required field '{key}'")
        if not schema.get("x-open"):
            for key in value:
                if key not in properties:
                    errors.append(f"{path}: unknown field '{key}' (no extra fields allowed)")
        for key, item in value.items():
            if key in properties:
                _check(item, properties[key], f"{path}.{key}", errors)
    elif isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            _check(item, schema["items"], f"{path}[{index}]", errors)


def _allows_null(schema: dict) -> bool:
    kind = schema.get("type")
    return kind == "null" or (isinstance(kind, list) and "null" in kind)


def _type_matches(value: object, kind) -> bool:
    kinds = kind if isinstance(kind, list) else [kind]
    return _json_type(value) in kinds or (_json_type(value) == "integer" and "number" in kinds)


def _json_type(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object" if isinstance(value, dict) else type(value).__name__


def _type_name(kind) -> str:
    return " or ".join(kind) if isinstance(kind, list) else kind
