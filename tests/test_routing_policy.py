"""Behavior and boundary tests for the pure routing policy."""
import math
import unittest
from copy import deepcopy

from storyloop.models import StoryLoopError
from storyloop.routing_policy import evaluate, max_tier, validate_policy
from tests.routing_support import signals


class PolicyTests(unittest.TestCase):
    def test_thresholds_and_guard_are_not_cumulative(self):
        policy = validate_policy(None)
        cases = [(1, "light", False), (4, "heavy", False),
                 (6, "heavy", False), (7, "heavy", True)]
        for stories, tier, split in cases:
            with self.subTest(stories=stories):
                result = evaluate(signals(stories=stories), policy)
                self.assertEqual((result["tier"], result["split"]), (tier, split))
        value = deepcopy(signals())
        value["risks"]["guard"] = "yes"
        self.assertEqual(evaluate(value, policy)["tier"], "standard")
        self.assertEqual(evaluate(value, policy), evaluate(value, policy))

    def test_size_boundaries_use_strict_split_and_inclusive_heavy(self):
        policy = validate_policy(None)
        cases = [
            ({"existing_files": 2}, "light", False),
            ({"existing_files": 3}, "standard", False),
            ({"existing_files": 7}, "standard", False),
            ({"existing_files": 8}, "heavy", False),
            ({"stories": 7}, "heavy", True),
            ({"stories": 8}, "heavy", True),
            ({"existing_files": 1, "created_files": 14}, "light", False),
            ({"existing_files": 1, "created_files": 15}, "heavy", True),
            ({"interfaces": 8}, "light", False),
            ({"interfaces": 9}, "heavy", True),
            ({"max_references": 29}, "light", False),
            ({"max_references": 30}, "heavy", False),
        ]
        for overrides, tier, split in cases:
            with self.subTest(overrides=overrides):
                result = evaluate(signals(**overrides), policy)
                self.assertEqual((result["tier"], result["split"]), (tier, split))

    def test_manual_ratio_compares_without_rounding(self):
        policy = validate_policy(None)
        self.assertEqual(evaluate(signals(criteria=4, manual=1), policy)["tier"], "light")
        self.assertEqual(evaluate(signals(criteria=4, manual=2), policy)["tier"], "heavy")

    def test_nature_and_risks_apply_their_documented_floors(self):
        policy = validate_policy(None)
        self.assertEqual(evaluate(signals(nature="refactor"), policy)["tier"], "standard")
        heavy = ("sensitive", "migration", "external_contract", "new_interface",
                 "crosscut", "principle_deviation", "hard_verify_high_cost")
        standard = ("dependency_swap", "persistent_state", "human_choice")
        for key in heavy:
            with self.subTest(risk=key):
                value = signals()
                value["risks"][key] = "yes"
                self.assertEqual(evaluate(value, policy)["tier"], "heavy")
        for key in standard:
            with self.subTest(risk=key):
                value = signals()
                value["risks"][key] = "yes"
                self.assertEqual(evaluate(value, policy)["tier"], "standard")

    def test_fix_conforming_to_existing_contract_can_remain_light(self):
        result = evaluate(signals(nature="fix"), validate_policy(None))
        self.assertEqual(result["tier"], "light")

    def test_unknown_risk_or_scope_is_unresolved_and_not_light(self):
        policy = validate_policy(None)
        risk = signals()
        risk["risks"]["sensitive"] = "unknown"
        self.assertEqual(evaluate(risk, policy)["tier"], "standard")
        self.assertEqual(evaluate(risk, policy)["unresolved"], ["risks.sensitive"])
        scope = evaluate(signals(existing_files=None), policy)
        self.assertEqual(scope["tier"], "standard")
        self.assertEqual(scope["unresolved"], ["existing_files"])

    def test_guard_raises_standard_to_heavy_and_records_original_value(self):
        value = signals(stories=2)
        value["risks"]["guard"] = "yes"
        result = evaluate(value, validate_policy(None))
        self.assertEqual(result["tier"], "heavy")
        self.assertIn({"rule_id": "GUARD", "fields": ["risks.guard"],
                       "values": ["yes"]}, result["reasons"])

    def test_reasons_identify_rule_fields_and_original_values(self):
        result = evaluate(signals(stories=7), validate_policy(None))
        self.assertIn({"rule_id": "SPLIT-SIZE", "fields": ["stories"],
                       "values": [7]}, result["reasons"])

    def test_policy_defaults_and_partial_overrides_are_isolated(self):
        first = validate_policy(None)
        second = validate_policy({"manual_ratio": 0.5})
        self.assertEqual(first["manual_ratio"], 0.25)
        self.assertEqual(second["manual_ratio"], 0.5)
        second["sensitive_paths"].append("private")
        self.assertEqual(first["sensitive_paths"], [])

    def test_invalid_policy_values_are_rejected(self):
        invalid = (
            {"unknown": 1},
            {"policy_version": True},
            {"split_stories": 0},
            {"split_files": "15"},
            {"heavy_stories": 7},
            {"heavy_existing": 16},
            {"manual_ratio": True},
            {"manual_ratio": math.nan},
            {"manual_ratio": 1.1},
            {"sensitive_paths": ["../secret"]},
            {"sensitive_paths": ["/secret"]},
            {"sensitive_paths": ["."]},
            {"guard_paths": ["safe/../../secret"]},
        )
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(StoryLoopError):
                validate_policy(value)

    def test_invalid_signals_types_and_counts_are_rejected(self):
        invalid = (
            signals(criteria=0), signals(criteria=True), signals(manual=-1),
            signals(criteria=3, manual=4), signals(stories=True),
            signals(complete="yes"), signals(risks={}),
        )
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(StoryLoopError):
                evaluate(value, validate_policy(None))

    def test_risk_object_key_order_is_irrelevant(self):
        value = signals()
        value["risks"] = dict(reversed(list(value["risks"].items())))
        self.assertEqual(evaluate(value, validate_policy(None))["tier"], "light")

    def test_max_tier_orders_known_tiers_and_rejects_invalid_calls(self):
        self.assertEqual(max_tier("light", "heavy", "standard"), "heavy")
        for tiers in ((), ("unknown",), ("light", "unknown")):
            with self.subTest(tiers=tiers), self.assertRaisesRegex(
                    StoryLoopError, "Invalid routing tier"):
                max_tier(*tiers)


if __name__ == "__main__":
    unittest.main()
