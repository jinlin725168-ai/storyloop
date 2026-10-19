import unittest

from storyloop.models import StoryLoopError
from storyloop.role_profiles import resolve_adapters, role_settings


class RoleProfileTests(unittest.TestCase):
    def test_legacy_adapter_becomes_default_without_changing_kind(self):
        resolved = resolve_adapters({"adapter": "command", "agent_command": ["agent"]})
        self.assertEqual(resolved, {"default": {"kind": "command", "agent_command": ["agent"]}})

    def test_role_override_changes_review_adapter_only(self):
        config = {"adapters": {"claude": {"kind": "manual"}, "codex": {"kind": "manual"}},
                  "roles": {"default_adapter": "claude", "review": {"adapter": "codex"}}}
        self.assertEqual(role_settings(config, "review", "standard")["adapter"], "codex")
        self.assertEqual(role_settings(config, "implement", "standard")["adapter"], "claude")

    def test_implement_uses_standard_only_for_light_routing(self):
        self.assertEqual(role_settings({}, "implement", "light")["tier"], "standard")
        self.assertEqual(role_settings({}, "implement", "standard")["tier"], "strong")

    def test_decision_role_downgrade_requires_reason(self):
        with self.assertRaisesRegex(StoryLoopError, "reason"):
            role_settings({"roles": {"review": {"tier": "standard"}}}, "review", "heavy")


if __name__ == "__main__":
    unittest.main()
