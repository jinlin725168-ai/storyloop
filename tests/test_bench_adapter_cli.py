"""Guardrail bench 2026-11-18: three ways a Claude bench run failed before any agent call."""
import json
import sys
import unittest

from storyloop.cli import parser
from storyloop.models import StoryLoopError

RUN = ["bench", "run", "case", "--adapter", "claude", "--model", "opus", "--base-engine-commit", "c",
       "--engine-repository", ".", "--repository", "r=.", "--engine-python", sys.executable]


class BenchAdapterCliTests(unittest.TestCase):
    def test_an_adapter_argv_with_dash_items_is_given_as_json(self):
        from storyloop.bench_cli import agent_command
        argv = [sys.executable, "-P", "-m", "storyloop.claude_agent", "--model", "{model}"]
        args = parser().parse_args([*RUN, "--agent-command-json", json.dumps(argv)])
        self.assertEqual(agent_command(args), argv)
        with self.assertRaises(SystemExit):  # the plain form cannot carry "-m"
            parser().parse_args([*RUN, "--agent-command", sys.executable, "-m", "storyloop.claude_agent"])
        with self.assertRaises(StoryLoopError):
            agent_command(parser().parse_args([*RUN, "--agent-command-json", '{"not": "a list"}']))

    def test_an_unadvertised_tier_names_the_fix(self):
        # The bench ran six attempts into ADAPTER_PROFILE_UNSUPPORTED with no hint: the adapter had
        # --model only, which (deliberately) claims no tier; --tier-model is the explicit mapping.
        from storyloop.capability_protocol import _require_tier
        with self.assertRaises(StoryLoopError) as refused:
            _require_tier("review", {"tier": "strong", "adapter": "bench"}, {"kind": "command"},
                          {"known": True, "tiers": []})
        self.assertIn("--tier-model strong=", str(refused.exception))
        self.assertIn("advertised: none", str(refused.exception))

    def test_init_writes_an_adapter_command_that_ignores_the_project_directory(self):
        from storyloop.init_detect import agent_config
        self.assertEqual(agent_config("claude")[1]["agent_command"][:3], ["{python}", "-P", "-m"])


if __name__ == "__main__":
    unittest.main()
