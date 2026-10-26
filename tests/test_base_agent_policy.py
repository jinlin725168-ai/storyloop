"""Base implementation defaults and agent role tiers are fixed policy."""

import unittest

from storyloop.base_run_open import choose_implementer
from storyloop.models import StoryLoopError
from storyloop.role_profiles import role_settings


class BaseAgentPolicyTests(unittest.TestCase):
    def test_foundation_and_skills_default_to_agent(self):
        self.assertEqual(choose_implementer("foundation", None), "agent")
        self.assertEqual(choose_implementer("skills", None), "agent")

    def test_ci_and_upstream_default_to_human(self):
        self.assertEqual(choose_implementer("ci", None), "human")
        self.assertEqual(choose_implementer("upstream", None), "human")

    def test_explicit_human_remains_available_for_foundation(self):
        self.assertEqual(choose_implementer("foundation", "human"), "human")

    def test_ci_rejects_explicit_agent(self):
        with self.assertRaisesRegex(StoryLoopError, "CI"):
            choose_implementer("ci", "agent")

    def test_heavy_base_role_rejects_lighter_implement_model(self):
        config = {"adapters": {"one": {"kind": "manual"}},
                  "roles": {"default_adapter": "one", "implement": {"tier": "standard"}}}
        with self.assertRaisesRegex(StoryLoopError, "base|Base|heavy|strong"):
            role_settings(config, "implement", "heavy", base=True)


if __name__ == "__main__":
    unittest.main()
