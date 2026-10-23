"""The type policy is immutable and preserves legacy product defaults."""

import unittest
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from unittest.mock import patch

from storyloop import change_types
from storyloop.change_types import CHANGE_TYPES, definition_for, type_of
from storyloop.models import StoryLoopError
from storyloop.project_loop_gates import ensure_characterization
from storyloop.requirement_checklist import evaluate_checklist
from tests.test_change_nature import fix_product, refactor_product


class ChangeTypeTests(unittest.TestCase):
    def test_existing_types_define_their_policy(self):
        expected = {
            "feature": ("light", "FEAT"),
            "fix": ("light", "FIX"),
            "refactor": ("standard", "REF"),
        }
        for nature, (tier, prefix) in expected.items():
            with self.subTest(nature=nature):
                policy = definition_for(nature)
                self.assertEqual((policy.minimum_tier, policy.id_prefix), (tier, prefix))
                self.assertTrue(policy.criteria_floor)
                self.assertEqual(policy.writable_scope, ("mutable", "test_roots"))
                self.assertTrue(policy.required_stages)
                self.assertEqual(policy.human_review, "by_tier")

    def test_default_and_unknown_types(self):
        self.assertEqual(type_of({}), "feature")
        self.assertEqual(type_of({"nature": "fix"}), "fix")
        with self.assertRaises(StoryLoopError):
            definition_for("unknown")

    def test_policy_cannot_be_changed_by_a_caller(self):
        with self.assertRaises(TypeError):
            CHANGE_TYPES["feature"] = definition_for("fix")
        with self.assertRaises((AttributeError, TypeError)):
            definition_for("feature").minimum_tier = "heavy"

    def test_checklist_uses_the_defined_defect_floor(self):
        product = fix_product()
        del product["stories"][0]["acceptance"][0]["reproduces_defect"]
        before = evaluate_checklist(product)
        self.assertTrue(any("reproduces the defect" in item["text"] and item["level"] == "error"
                            for item in before))
        policy = replace(CHANGE_TYPES["fix"], criteria_floor=("acceptance_red",))
        with patch.object(change_types, "CHANGE_TYPES", MappingProxyType({**CHANGE_TYPES, "fix": policy})):
            after = evaluate_checklist(product)
        self.assertFalse(any("reproduces the defect" in item["text"] for item in after))

    def test_characterization_gate_uses_the_defined_required_stages(self):
        policy = replace(CHANGE_TYPES["refactor"], required_stages=("by_tier",))
        state = {"routing": {"version": 1, "effective_tier": "standard"}, "planning": {}}
        config = {"profile": {"test_roots": []}}
        with patch.object(change_types, "CHANGE_TYPES", MappingProxyType({**CHANGE_TYPES, "refactor": policy})):
            with patch("storyloop.project_loop_gates.save_run"):
                ensure_characterization(Path("."), {"product": refactor_product()}, state,
                                        SimpleNamespace(characterize=None), config)
        self.assertEqual(state["characterization"]["status"], "DEFERRED")
