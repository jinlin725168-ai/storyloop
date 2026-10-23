"""The type policy is immutable and preserves legacy product defaults."""

import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from unittest.mock import patch
import tempfile

from storyloop import change_types
from storyloop.change_types import CHANGE_TYPES, definition_for, type_of
from storyloop.models import StoryLoopError
from storyloop.changes import _next_id
from storyloop.intake import _status
from storyloop.planning import required_locks
from storyloop.project_loop_gates import ensure_characterization
from storyloop.requirement_checklist import evaluate_checklist
from storyloop.routing_policy import evaluate, validate_policy
from storyloop.routing_state import open_routing
from tests.test_change_nature import fix_product, refactor_product
from tests.routing_support import signals


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

    def test_routing_respects_the_type_minimum_tier(self):
        policy = replace(CHANGE_TYPES["feature"], minimum_tier="heavy")
        with patch.object(change_types, "CHANGE_TYPES", MappingProxyType({**CHANGE_TYPES, "feature": policy})):
            decision = evaluate(signals(nature="feature"), validate_policy(None))
        self.assertEqual(decision["tier"], "heavy")

    def test_run_cannot_open_below_the_type_floor(self):
        change = {"approval": {"tier": "light"}, "intake": {"decision": {"reasons": []}},
                  "product": {"nature": "refactor", "assessment": {"scope": {}}}}
        with self.assertRaisesRegex(StoryLoopError, "type floor"):
            open_routing(change, validate_policy(None), {})

    def test_required_locks_come_from_the_type_stages(self):
        policy = replace(CHANGE_TYPES["refactor"], required_stages=("by_tier",))
        with patch.object(change_types, "CHANGE_TYPES", MappingProxyType({**CHANGE_TYPES, "refactor": policy})):
            paths = required_locks("standard", "refactor", ["app/a.py"])
        self.assertEqual(paths, [])

    def test_change_id_prefix_comes_from_the_type(self):
        policy = replace(CHANGE_TYPES["fix"], id_prefix="DEMO")
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(change_types, "CHANGE_TYPES", MappingProxyType({**CHANGE_TYPES, "fix": policy})):
                change_id = _next_id(Path(folder), datetime(2026, 10, 23, tzinfo=timezone.utc), "fix")
        self.assertEqual(change_id, "DEMO-20261023-001")

    def test_intake_existing_criteria_rule_comes_from_the_type(self):
        policy = replace(CHANGE_TYPES["feature"], requires_new_criteria=False)
        product = {"nature": "feature", "questions": [],
                   "stories": [{"acceptance": [{"id": "A1", "kind": "automated"}]}],
                   "assessment": {"verifiable_goal": True, "criteria": [
                       {"existence": "exists", "feasibility": "local_automated",
                        "method": {"commands": []}, "criterion_id": "A1"}]}}
        with patch.object(change_types, "CHANGE_TYPES", MappingProxyType({**CHANGE_TYPES, "feature": policy})):
            status = _status(Path("."), "ignored", product, {"unresolved": []})
        self.assertEqual(status, "READY")
