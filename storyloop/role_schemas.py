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

from .adapter_capabilities import ROLES
from .lessons import ACTIONS, LANDING_KINDS, MAX_SUMMARY, STATUSES, STRENGTHS, TIERS

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
    if 'anyOf' in value:
        value['anyOf'].append({'type': 'null'})
    return value


FILES = {"type": "array", "items": _obj({"path": STRING, "content": STRING})}
SYSTEM_SMOKE_TEST = _obj({"id": STRING, "description": STRING, "command": STRINGS,
                          "timeout_seconds": {"type": "integer"}})
QUESTION = _obj({"id": STRING, "text": STRING, "options": STRINGS,
                 "recommended": _nullable(STRING), "why": _nullable(STRING)})
ROW_TYPE = {"type": "string", "enum": ["positive", "degradation", "unknown_state", "form_parity",
                                       "regression", "state_preservation", "non_goal"]}
ACCEPTANCE_TEST = _obj({"id": STRING, "criteria": STRINGS, "command": STRINGS,
                        "timeout_seconds": {"type": "integer"},
                        "tier": _nullable({"type": "string", "enum": ["fast", "heavy"]})})
CRITERION = _obj({"id": STRING, "given": STRING, "when": STRING, "then": STRING,
                  "kind": {"type": "string", "enum": ["automated", "manual"]},
                  "row_type": _nullable(ROW_TYPE),
                  "oracle_class": _nullable({"type": "string", "enum": ["auto-fast", "auto-heavy", "manual"]}),
                  "consumers": _nullable(STRINGS),
                  "reproduces_defect": _nullable({"type": "boolean"})})
DEFECT = _obj({"observed": STRING, "expected": STRING, "reproduction": STRINGS,
               "reporter_hypothesis": _nullable(STRING),
               "root_cause": {"type": "array", "items": _obj({"statement": STRING, "location": STRING})},
               "confirmed": STRINGS, "unconfirmed": STRINGS,
               "sibling_entries": _nullable(STRINGS), "origin_change": _nullable(STRING)})
REFACTOR = _obj({"subtype": {"type": "string", "enum": ["structure", "dependency-swap"]},
                 "scope": STRINGS, "invariants": STRINGS, "structural_goals": STRINGS,
                 "migration_map": _nullable({"type": "array", "items": _obj(
                     {"from": STRING, "to": STRING, "assertion": STRING})})})
CONSUMER = _obj({"id": STRING, "consumer": STRING, "trigger": STRING, "path": STRING,
                 "in_scope": {"type": "boolean"}, "verification": STRING})
STORY = _obj({
    "id": STRING, "title": STRING,
    "priority": _nullable({"type": "string", "enum": ["P1", "P2", "P3"]}),
    "independent_test": _nullable(STRING),
    "as_a": STRING, "want": STRING, "so_that": STRING, "rules": STRINGS,
    "out_of_scope": STRINGS, "depends_on": STRINGS,
    "acceptance": {"type": "array", "items": CRITERION},
})
FINDING = _obj({"message": STRING, "perspective": _nullable(STRING),
                "criterion_id": _nullable(STRING), "evidence": _nullable(STRING),
                "target": _nullable({"type": "string", "enum": ["implementation", "oracle", "contract", "out_of_scope"]}),
                "location": _nullable(STRING)}, open_=True)
FACTS = _nullable({"type": "array", "items": _obj({"statement": STRING, "location": STRING})})

EVIDENCE = _obj({'locations': STRINGS, 'search_scope': STRINGS})
METHOD = _obj({'commands': {'type': 'array', 'items': STRINGS},
               'steps': STRINGS, 'expected': STRING})
ASSESSMENT_CRITERION = _obj({
    'story_id': STRING, 'criterion_id': STRING,
    'feasibility': {'type': 'string', 'enum': ['local_automated', 'manual', 'infeasible', 'unknown']},
    'existence': {'type': 'string', 'enum': ['exists', 'partial', 'absent', 'unknown']},
    'method': METHOD, 'evidence': EVIDENCE})
from .routing_policy import RISK_FIELDS
ASSESSMENT = _obj({
    'verifiable_goal': {'type': 'boolean'},
    'scope': _obj({'touched': STRINGS, 'creates': STRINGS, 'interfaces': STRINGS,
                   'symbols': {'type': 'array', 'items': _obj({'path': STRING, 'symbol': STRING})}}),
    'risks': _obj({key: _obj({'value': {'type': 'string', 'enum': ['yes', 'no', 'unknown']},
                              'evidence': EVIDENCE}) for key in RISK_FIELDS}),
    'criteria': {'type': 'array', 'items': ASSESSMENT_CRITERION}})

PLAN_OPTION = _obj({
    'id': STRING, 'summary': STRING,
    **ASSESSMENT['properties']['scope']['properties'],
    'risks': ASSESSMENT['properties']['risks'], 'forbidden_paths': STRINGS,
    'constraints': {'type': 'array', 'items': _obj(
        {'criterion_id': STRING, 'path': STRING, 'rule': STRING})},
    'regression': STRINGS, 'migration': _obj({'steps': STRINGS, 'reason': STRING}),
    'dependencies': {'type': 'array', 'items': _obj({
        'ecosystem': STRING, 'name': STRING, 'version': STRING,
        'verification': _obj({'check_index': {'type': 'integer'}})})},
    'principle_checks': {'type': 'array', 'items': _obj({
        'principle_id': STRING, 'verdict': {'type': 'string', 'enum': [
            'complies', 'deviates', 'not_applicable', 'unknown']}, 'reason': STRING})}})
ROUTING_PLAN = _obj({'main_option': STRING,
                    'options': {'type': 'array', 'items': PLAN_OPTION},
                    'questions': STRINGS, 'design': STRING, 'facts': FACTS})

CONSISTENCY = _obj({
    'verdict': {'type': 'string', 'enum': ['coherent', 'blocked', 'needs_input']},
    'summary': STRING, 'questions': STRINGS,
    'findings': {'type': 'array', 'items': _obj({
        'target': {'type': 'string', 'enum': ['contract', 'oracle', 'design']},
        'criterion_id': STRING, 'location': STRING, 'blocking': {'type': 'boolean'},
        'message': STRING})}})

SCHEMAS = {
    "product": _obj({
        "version": {"type": "integer", "enum": [1]}, "goal": STRING, "non_goals": STRINGS,
        "assessment": _nullable(ASSESSMENT),
        "capability": _nullable(STRING),
        "clarifications": _nullable({"type": "array", "items": _obj(
            {"session": STRING, "question": STRING, "answer": STRING})}),
        "questions": {"type": "array", "items": QUESTION},
        "stories": {"type": "array", "items": STORY},
        "nature": _nullable({"type": "string", "enum": ["feature", "fix", "refactor"]}),
        "defect": _nullable(DEFECT),
        "refactor": _nullable(REFACTOR),
        "consumers": _nullable({"type": "array", "items": CONSUMER}),
        "coverage_exemptions": _nullable({"type": "array", "items": _obj(
            {"row_type": ROW_TYPE, "reason": STRING})}),
        "test_removals": _nullable({"type": "array", "items": _obj({"path": STRING, "reason": STRING})}),
    }),
    "acceptance": _obj({"files": FILES, "manifest": _obj({
        "version": {"type": "integer", "enum": [1]},
        "tests": {"type": "array", "items": ACCEPTANCE_TEST}})}),
    "smoke-maintenance": _obj({"files": FILES, "manifest": _obj({
        "version": {"type": "integer", "enum": [1]},
        "tests": {"type": "array", "items": SYSTEM_SMOKE_TEST}}),
        "rationale": _nullable(STRING)}),
    "design": _obj({"summary": STRING, "touched": STRINGS, "design": STRING, "questions": STRINGS,
                    "facts": FACTS}),
    "characterize": _obj({"summary": STRING, "touched": STRINGS, "tests": STRINGS, "questions": STRINGS}),
    "implement": _obj({"summary": STRING, "questions": STRINGS, "design": _nullable(STRING),
                       "facts": FACTS}),
    "review": _obj({"verdict": {"type": "string", "enum": ["pass", "fail", "needs_input"]},
                    "summary": STRING, "findings": {"type": "array", "items": FINDING},
                    "questions": STRINGS, "facts": FACTS}),
    "survey": _obj({"documents": FILES}),
}


REVIEW_FIELDS = {"severity": {"type": "string", "enum": ["P0", "P1", "P2", "question"]},
                 "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
                 "introduced": {"type": "boolean"}, "trigger": STRING, "impact": STRING, "fix": STRING}
CHECKLIST = {"type": "array", "items": _obj({"decision": STRING,
             "status": {"type": "string", "enum": ["pass", "issue", "unconfirmed"]}})}
SCHEMAS["review"]["properties"].update(coverage=_nullable(STRINGS), checklist=_nullable(CHECKLIST))
SCHEMAS["review"]["required"] = list(SCHEMAS["review"]["properties"])
VERIFY = _obj({"results": {"type": "array", "items": _obj({"id": STRING,
               "confirmed": {"type": "boolean"}, "reason": STRING})}})


def _profiled_review(schema):
    schema = copy.deepcopy(schema)
    finding = schema['properties']['findings']['items']
    finding['properties'].update(REVIEW_FIELDS)
    finding['properties']['id'] = _nullable(STRING)
    finding['required'] = list(finding['properties'])
    schema['properties'].update(coverage=STRINGS, checklist=_nullable(CHECKLIST))
    schema['required'] = list(schema['properties'])
    return schema


def _retrospective_landing(write: bool) -> dict:
    """Keep references separate from writes; unused strict-wire fields must be null."""
    fields = {'path': STRING, 'argv': STRINGS}
    if write:
        fields.update(append=STRING, change=STRING, principle=_obj({'id': STRING, 'text': STRING}))
        variants = {'foundation': {'path', 'append'}, 'check_command': {'argv'},
                    'principles': {'principle', 'change'}}
        optional = set()
    else:
        fields.update(anchor=STRING, name=STRING)
        variants = {'foundation': {'path', 'anchor'}, 'check_command': {'argv', 'name'}, 'principles': set()}
        optional = {'anchor', 'name'}
    schema = _obj({'kind': {'type': 'string', 'enum': list(LANDING_KINDS)},
                   **{key: _nullable(value) for key, value in fields.items()}})
    schema['anyOf'] = [_obj({'kind': {'type': 'string', 'enum': [kind]}, **{
        key: (_nullable(value) if key in optional else value) if key in allowed else {'type': 'null'}
        for key, value in fields.items()}}) for kind, allowed in variants.items()]
    return schema


RETROSPECTIVE_REFERENCE = _retrospective_landing(write=False)
RETROSPECTIVE_LANDING = _retrospective_landing(write=True)
RETROSPECTIVE_ENTRY = _obj({
    'summary': {'type': 'string', 'minLength': 1, 'maxLength': MAX_SUMMARY,
                'description': 'Nonempty single-line summary, at most 120 Unicode characters. Put details in body.'},
    'tier': {'type': 'string', 'enum': list(TIERS)}, 'scope': STRINGS,
    'roles': {'type': 'array', 'items': {'type': 'string', 'enum': list(ROLES)}},
    'strength': {'type': 'string', 'enum': list(STRENGTHS)},
    'source': STRING, 'baseline_commit': STRING,
    'status': {'type': 'string', 'enum': list(STATUSES)}, 'updated_at': STRING,
    'supersedes': STRINGS, 'landings': {'type': 'array', 'items': RETROSPECTIVE_REFERENCE,
        'description': 'References, not write instructions. Required for every non-prose strength.'}})
# Native generation enforces the same non-prose reference requirement as parse_lesson.
# Keep properties available for normalization and deferred exact-field validation.
_prose_entry = copy.deepcopy(RETROSPECTIVE_ENTRY)
_prose_entry['properties']['strength']['enum'] = ['prose']
_landed_entry = copy.deepcopy(RETROSPECTIVE_ENTRY)
_landed_entry['properties']['strength']['enum'] = [item for item in STRENGTHS if item != 'prose']
_landed_entry['properties']['landings']['minItems'] = 1
RETROSPECTIVE_ENTRY['anyOf'] = [_prose_entry, _landed_entry]
RETROSPECTIVE = _obj({
    'lessons': {'type': 'array', 'items': _obj({
        'action': {'type': 'string', 'enum': list(ACTIONS)}, 'target': _nullable(STRING),
        'entry': RETROSPECTIVE_ENTRY, 'body': STRING, 'why_needed': STRING, 'evidence': STRINGS,
        'landing': {**_nullable(RETROSPECTIVE_LANDING), 'description':
            'Write instruction, distinct from entry.landings references. For knowledge use foundation or principles; '
            'for check use check_command. For prose use null. Unused fields must be null.'}})},
    'errata': {'type': 'array', 'items': _obj({'document': STRING, 'claim': STRING,
        'correction': STRING, 'evidence': STRINGS})},
    'uncovered_concepts': {'type': 'array', 'items': _obj({'concept': STRING, 'evidence': STRINGS,
        'suggested_document': STRING})}, 'nothing_to_learn': _nullable(STRING)})
# Retrospective proposals are independently validated/dropped after the read-only guard.
# Keep their strict wire shape; never weaken the top-level response or ordinary roles.
for _proposal_field in ('lessons', 'uncovered_concepts'):
    RETROSPECTIVE['properties'][_proposal_field]['items']['x-deferred-validation'] = True


def schema_for(role: str, payload: dict) -> dict:
    """Add optional memory metadata to every ordinary call, including special modes."""
    schema = copy.deepcopy(_selected_schema(role, payload))
    if role != 'survey' and payload.get('mode') != 'retrospective':
        schema['properties'].update(
            lessons_used=_nullable({'type': 'array', 'items': _obj({
                'id': STRING, 'how': {'type': 'string', 'x-maxLength': 200}})}),
        )
    if payload.get('mode') != 'retrospective':
        schema['properties'].update(errata=_nullable({'type': 'array', 'items': _obj({
                'document': STRING, 'claim': STRING, 'correction': STRING, 'evidence': STRINGS})}))
        schema['required'] = list(schema['properties'])
    return schema


def _selected_schema(role: str, payload: dict) -> dict:
    """The engine-side schema of one call (acceptance changes shape in smoke-maintenance mode)."""
    if role == "review" and payload.get("mode") == "retrospective":
        return RETROSPECTIVE
    if role == "review" and payload.get("mode") == "verify":
        return VERIFY
    if role == "acceptance" and payload.get("mode") == "smoke-maintenance":
        return SCHEMAS["smoke-maintenance"]
    if role == "design" and payload.get("mode") == "routing-plan":
        return ROUTING_PLAN
    if role == 'review' and payload.get('mode') == 'consistency':
        return CONSISTENCY
    if role in {"implement", "review"} and payload.get("routing_tier"):
        schema = copy.deepcopy(SCHEMAS[role])
        schema["properties"]["routing_risks"] = _nullable({"type": "array", "items": _obj({
            "risk": {"type": "string", "enum": list(RISK_FIELDS)},
            "statement": STRING, "location": STRING})})
        schema["required"].append("routing_risks")
        return _profiled_review(schema) if role == "review" and payload.get("role_profile") else schema
    if role == "review" and payload.get("role_profile"):
        return _profiled_review(SCHEMAS[role])
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
    if schema.get('x-deferred-validation'):
        return
    if len(errors) >= MAX_ERRORS:
        return
    if not _type_matches(value, schema["type"]):
        errors.append(f"{path}: expected {_type_name(schema['type'])}, got {_type_name(_json_type(value))}")
        return
    if 'anyOf' in schema and not any(
            not schema_errors(value, branch, path) for branch in schema['anyOf']):
        errors.append(f"{path}: does not match any allowed variant")
        return
    if type(value) in (int, float) and (value < schema.get("minimum", value) or value > schema.get("maximum", value)):
        errors.append(f"{path}: number outside allowed range")
    if isinstance(value, str):
        limit = min(schema.get('maxLength', len(value)), schema.get('x-maxLength', len(value)))
        if len(value) > limit or len(value) < schema.get('minLength', 0):
            errors.append(f"{path}: text outside allowed length")
    if isinstance(value, list) and len(value) < schema.get('minItems', 0):
        errors.append(f"{path}: too few items")
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
