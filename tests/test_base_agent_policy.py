"""Base implementation defaults and agent role tiers are fixed policy."""

import json
import unittest

from storyloop.base_run_open import choose_implementer
from storyloop.agent_transport import conform
from storyloop.models import StoryLoopError
from storyloop.role_profiles import role_settings
from storyloop.role_schemas import schema_for


class BaseAgentPolicyTests(unittest.TestCase):
    def test_adapter_handshake_vendor_is_normalized_before_cache(self):
        from storyloop.capability_protocol import _response
        raw = {"known": True, "vendor": " OpenAI ", "tiers": ["strong"],
               "subagents": False, "path_guard": True, "fast_checks": False,
               "modes": ["*"]}
        self.assertEqual(_response(json.dumps(raw))["vendor"], "openai")

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

    def test_base_design_answer_has_implementation_and_rollback_fields(self):
        schema = schema_for("design", {"mode": "base-plan"})
        answer = {"summary": "更新构建", "steps": ["修改 pom.xml"],
                  "risks": ["旧构建失败"], "rollback": "恢复旧 pom.xml", "questions": []}
        _, errors = conform(answer, schema)
        self.assertEqual(errors, [])

    def test_base_review_rejects_same_or_missing_handshake_vendor(self):
        from storyloop.base_review import require_distinct_vendors
        with self.assertRaisesRegex(StoryLoopError, "vendor|Vendor"):
            require_distinct_vendors("openai", "openai")
        with self.assertRaisesRegex(StoryLoopError, "vendor|Vendor"):
            require_distinct_vendors("openai", None)
        self.assertEqual(require_distinct_vendors("OpenAI", "Anthropic"),
                         ("openai", "anthropic"))

    def test_base_review_never_uses_degrade_fallback(self):
        from storyloop.cross_vendor import base_review_fallback_allowed
        self.assertFalse(base_review_fallback_allowed({
            "cross_vendor_review": {"enabled": True, "on_unavailable": "degrade"}},
            {"mode": "base-review"}))

    def test_human_substitute_metric_excludes_e1_review(self):
        from storyloop.metrics_compute import base_human_review_substitutions
        result = base_human_review_substitutions([
            {"change_id": "BASE-1", "base_review_mode": "human_e1"},
            {"change_id": "BASE-2", "base_review_mode": "human_substitute"},
            {"change_id": "BASE-3", "base_review_mode": "agent"},
        ])
        self.assertEqual(result["numerator"], 1)
        self.assertEqual(result["changes"], ["BASE-2"])


if __name__ == "__main__":
    unittest.main()
