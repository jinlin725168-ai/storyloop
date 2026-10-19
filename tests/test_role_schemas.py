"""Role schemas: one source for the engine check and for the agent CLIs' native enforcement."""

from __future__ import annotations

import unittest

from storyloop.project_agents import ROLES
from storyloop.role_schemas import SCHEMAS, normalize, schema_errors, schema_for, wire_schema
from tests.support import product


def _objects(schema: dict):
    if isinstance(schema, dict):
        if schema.get("type") == "object" or (isinstance(schema.get("type"), list) and "object" in schema["type"]):
            yield schema
        for value in schema.values():
            yield from _objects(value)
    elif isinstance(schema, list):
        for value in schema:
            yield from _objects(value)


class RoleSchemaTests(unittest.TestCase):
    def test_every_role_has_a_schema_in_the_strict_subset_both_clis_accept(self):
        self.assertTrue(ROLES <= set(SCHEMAS))
        for name, schema in SCHEMAS.items():
            wire = wire_schema(schema)
            for node in _objects(wire):
                with self.subTest(role=name):
                    self.assertIs(node["additionalProperties"], False)
                    self.assertEqual(sorted(node["required"]), sorted(node["properties"]))
                    self.assertFalse(any(key.startswith("x-") for key in node))

    def test_optional_fields_may_be_null_or_absent_and_nulls_are_dropped(self):
        answer = {"summary": "done", "questions": [], "design": None}
        schema = SCHEMAS["implement"]
        self.assertEqual(schema_errors(answer, schema), [])
        self.assertEqual(normalize(answer, schema), {"summary": "done", "questions": []})
        self.assertEqual(schema_errors({"summary": "done", "questions": []}, schema), [])

    def test_the_test_product_fixture_conforms(self):
        self.assertEqual(schema_errors(product(), SCHEMAS["product"]), [])

    def test_mismatches_are_reported_with_their_path(self):
        errors = schema_errors({"summary": 3, "questions": ["q"], "notes": "x"}, SCHEMAS["implement"])
        self.assertIn("$.summary: expected string, got integer", errors)
        self.assertIn("$: unknown field 'notes' (no extra fields allowed)", errors)
        review = {"verdict": "maybe", "summary": "s", "findings": [], "questions": []}
        self.assertEqual(schema_errors(review, SCHEMAS["review"]),
                         ["$.verdict: must be one of 'pass', 'fail', 'needs_input'"])

    def test_review_findings_stay_open_to_extra_keys(self):
        review = {"verdict": "fail", "summary": "s", "questions": [],
                  "findings": [{"message": "m", "criterion_id": "A1", "severity": "high"}]}
        self.assertEqual(schema_errors(normalize(review, SCHEMAS["review"]), SCHEMAS["review"]), [])

    def test_smoke_maintenance_uses_the_system_smoke_shape(self):
        schema = schema_for("acceptance", {"mode": "smoke-maintenance"})
        test = schema["properties"]["manifest"]["properties"]["tests"]["items"]
        self.assertIn("description", test["properties"])
        self.assertNotIn("criteria", test["properties"])
        self.assertIs(schema_for("acceptance", {}), SCHEMAS["acceptance"])


class FindingTargetTests(unittest.TestCase):
    def test_a_finding_may_name_its_target_and_location(self):
        from storyloop.role_schemas import normalize, schema_errors, schema_for
        schema = schema_for("review", {})
        body = normalize({"verdict": "fail", "summary": "s", "questions": [], "findings": [
            {"message": "m", "perspective": None, "criterion_id": "A33", "evidence": None,
             "target": "oracle", "location": "acceptance/changes/X/t.py:70"}]}, schema)
        self.assertEqual(schema_errors(body, schema), [])

    def test_an_unknown_target_is_rejected(self):
        from storyloop.role_schemas import normalize, schema_errors, schema_for
        schema = schema_for("review", {})
        body = normalize({"verdict": "fail", "summary": "s", "questions": [], "findings": [
            {"message": "m", "target": "someone-else"}]}, schema)
        self.assertTrue(schema_errors(body, schema))


if __name__ == "__main__":
    unittest.main()
