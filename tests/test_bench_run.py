"""The bench scheduler is tested with a stub, never a real agent."""

from pathlib import Path
import tempfile
import unittest

from storyloop.bench_run import run_bench, compare_runs
from tests.test_bench_case import case_fixture
from storyloop.models import StoryLoopError


class BenchRunTests(unittest.TestCase):
    def test_interleaved_attempts_and_nonroutine_stop(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = case_fixture(root)
            calls = []

            def adapter(side, case, attempt, human):
                calls.append((side, attempt))
                self.assertEqual(human({"kind": "contract_approval"}), "APPROVE")
                self.assertEqual(human({"kind": "replan"}), "NEEDS_HUMAN")
                events = [{"kind": "contract_approval"}]
                if side == "candidate" and attempt == 1:
                    events.append({"kind": "replan"})
                return {"status": "PASSED", "events": events, "calls": 2,
                        "agent_ms": 100, "wall_ms": 120,
                        "known_cost_usd": 0.1, "cost_coverage": 1.0}

            result = run_bench([source], "a" * 40, "b" * 40, "test", "fixture",
                               {"max_calls": 10}, adapter, lambda *_: True, root)
            self.assertEqual(calls, [("base", 1), ("candidate", 1),
                                     ("base", 2), ("candidate", 2)])
            attempts = result["cases"][0]["attempts"]
            self.assertEqual(attempts[1]["verdict"], "NEEDS_HUMAN")
            self.assertEqual(attempts[1]["nonroutine_types"], {"replan": 1})
            self.assertEqual(attempts[0]["verdict"], "PASSED")

    def test_missing_cost_and_config_mismatch_are_not_comparable(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = case_fixture(root)
            adapter = lambda *_: {"status": "PASSED", "events": [], "calls": 1,
                                  "agent_ms": 1, "wall_ms": 2,
                                  "known_cost_usd": None, "cost_coverage": None}
            result = run_bench([source], "a" * 40, "b" * 40, "test", "fixture",
                               {"max_calls": 10}, adapter, lambda *_: True, root)
            self.assertEqual(result["cases"][0]["comparison"]["status"], "NOT_COMPARABLE")
            self.assertIn("cost", result["cases"][0]["comparison"]["reasons"])
            other = {**result, "model": "different"}
            self.assertEqual(compare_runs(result, other)["status"], "NOT_COMPARABLE")
            self.assertIn("model", compare_runs(result, other)["reasons"])
            other = {**result, "case_version": 2}
            self.assertIn("case_version", compare_runs(result, other)["reasons"])

    def test_real_adapter_requires_explicit_cost_confirmation(self):
        with tempfile.TemporaryDirectory() as folder:
            source = case_fixture(Path(folder))
            with self.assertRaises(StoryLoopError):
                run_bench([source], "a" * 40, "b" * 40, "codex", "configured",
                          {"max_calls": 10}, lambda *_: {}, lambda *_: True, Path(folder))

    def test_cli_defaults_to_refusing_real_run(self):
        import subprocess
        import sys

        result = subprocess.run([sys.executable, "-m", "storyloop.cli", "bench", "run", "example",
                                 "--adapter", "codex", "--model", "configured"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("--i-accept-cost", result.stderr)


if __name__ == "__main__":
    unittest.main()
