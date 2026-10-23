"""The type policy is immutable and preserves legacy product defaults."""

import unittest

from storyloop.change_types import CHANGE_TYPES, definition_for, type_of
from storyloop.models import StoryLoopError


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
