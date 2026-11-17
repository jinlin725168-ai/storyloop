"""A role whose adapter is unavailable may fall back to a configured second adapter (roadmap C2).

2026-11-16/17: Codex hit its usage limit mid-bench and a Claude OAuth session expired mid-run;
each stopped the run although another configured agent could have answered the same request.
"""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from storyloop.cross_vendor import invoke
from storyloop.models import StoryLoopError
from storyloop.role_profiles import role_settings

ADAPTERS = {"claude": {"kind": "command", "agent_command": ["claude-agent"]},
            "codex": {"kind": "codex"}}


def config(roles):
    return {"adapters": ADAPTERS, "roles": roles}


def unavailable(message="exit 1 | Failed to authenticate: OAuth session expired"):
    return StoryLoopError(f"Project agent process failed safely ({message})", code="AGENT_UNAVAILABLE")


class FallbackSettingsTests(unittest.TestCase):
    def test_role_and_default_fallback_resolve_and_must_be_known_and_distinct(self):
        roles = {"default_adapter": "claude", "default_fallback_adapter": "codex",
                 "review": {"adapter": "codex", "fallback_adapter": "claude"}}
        self.assertEqual(role_settings(config(roles), "implement", None)["fallback_adapter"], "codex")
        self.assertEqual(role_settings(config(roles), "review", None)["fallback_adapter"], "claude")
        self.assertNotIn("fallback_adapter", role_settings(config({"default_adapter": "claude"}),
                                                           "implement", None))
        for bad in ({"default_adapter": "claude", "default_fallback_adapter": "missing"},
                    {"default_adapter": "claude", "implement": {"fallback_adapter": "claude"}}):
            with self.subTest(roles=bad), self.assertRaises(StoryLoopError):
                role_settings(config(bad), "implement", None)
        # A fallback equal to the role's own adapter is ignored for the default, not an error.
        same = {"default_adapter": "codex", "default_fallback_adapter": "codex"}
        self.assertNotIn("fallback_adapter", role_settings(config(same), "implement", None))


class FallbackInvokeTests(unittest.TestCase):
    def run_invoke(self, roles, outcomes):
        calls = []

        def call(settings, degraded):
            calls.append((settings["adapter"], degraded))
            outcome = outcomes[len(calls) - 1]
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        with tempfile.TemporaryDirectory() as folder, \
                patch("storyloop.adapter_capabilities.ensure_checked"), \
                patch("storyloop.adapter_capabilities.require_role_target"):
            result = invoke(Path(folder), "implement", {}, config(roles), "FEAT-1", call)
        return result, calls

    def test_unavailable_primary_retries_once_on_the_fallback_and_marks_the_route(self):
        roles = {"default_adapter": "claude", "default_fallback_adapter": "codex"}
        result, calls = self.run_invoke(roles, [unavailable(), {"summary": "done"}])
        self.assertEqual(result, {"summary": "done"})
        self.assertEqual(calls, [("claude", None), ("codex", "fallback")])

    def test_failing_fallback_reports_both_and_stays_unavailable(self):
        roles = {"default_adapter": "claude", "default_fallback_adapter": "codex"}
        with self.assertRaises(StoryLoopError) as caught:
            self.run_invoke(roles, [unavailable(), unavailable("exit 1 | usage limit")])
        self.assertEqual(caught.exception.code, "AGENT_UNAVAILABLE")
        self.assertEqual(caught.exception.details["fallback_adapter"], "codex")
        self.assertIn("OAuth", caught.exception.details["primary_error"])

    def test_judged_answers_and_unconfigured_roles_never_fall_back(self):
        roles = {"default_adapter": "claude", "default_fallback_adapter": "codex"}
        invalid = StoryLoopError("schema", code="AGENT_CONTRACT_INVALID")
        with self.assertRaises(StoryLoopError):
            self.run_invoke(roles, [invalid, {"summary": "never"}])
        with self.assertRaises(StoryLoopError):
            self.run_invoke({"default_adapter": "claude"}, [unavailable(), {"summary": "never"}])


class FallbackReportTests(unittest.TestCase):
    def test_report_names_each_fallback_call(self):
        from storyloop.report_roles import conclusion_items
        evidence = {"run": {}, "calls": [
            {"call": "c1", "role": "implement", "adapter": "codex", "degraded": "fallback",
             "fallback_from": "claude"}]}
        with patch("storyloop.report_roles.calls", return_value=evidence["calls"]):
            items = {entry["id"]: entry for entry in conclusion_items(evidence)}
        self.assertEqual(items["fallback_routes"]["data"],
                         [{"call": "c1", "role": "implement", "from": "claude", "to": "codex"}])


if __name__ == "__main__":
    unittest.main()
