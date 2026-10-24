"""State format declarations used by both schemas and runtime validation."""

import importlib
import importlib.util
import unittest

from storyloop import lessons, onboarding_manifest, onboarding_templates, oracle_counts, routing_policy
from storyloop.role_schemas import schema_for


class StateFormatTests(unittest.TestCase):
    def test_frozen_enums_and_patterns_have_one_contract_source(self):
        self.assertIsNotNone(importlib.util.find_spec("storyloop.state_format"))
        fmt = importlib.import_module("storyloop.state_format")
        self.assertIs(lessons.TIERS, fmt.TIERS)
        self.assertIs(lessons.ACTIONS, fmt.ACTIONS)
        self.assertIs(routing_policy.RISK_FIELDS, fmt.RISK_FIELDS)
        self.assertIs(oracle_counts.validate_patterns, fmt.validate_test_count_patterns)
        self.assertEqual(fmt.TIERS, ("core", "normal"))
        self.assertEqual(fmt.ACTIONS, ("create", "update", "retire"))
        self.assertEqual(fmt.ONBOARDING_STACKS, ("maven", "node", "python", "generic"))
        self.assertEqual(set(onboarding_templates.HINTS), set(fmt.ONBOARDING_STACKS))
        self.assertIs(onboarding_manifest.HOOKS, fmt.HOOKS)
        retrospective = schema_for("review", {"mode": "retrospective"})
        proposal = retrospective["properties"]["lessons"]["items"]
        self.assertEqual(proposal["properties"]["action"]["enum"], list(fmt.ACTIONS))

    def test_custom_test_count_pattern_keeps_its_named_group_contract(self):
        self.assertIsNotNone(importlib.util.find_spec("storyloop.state_format"))
        fmt = importlib.import_module("storyloop.state_format")
        pattern = [{"match": "suite", "executed": r"Ran (?P<executed>\d+) cases"}]
        self.assertEqual(fmt.validate_test_count_patterns(pattern), pattern)
