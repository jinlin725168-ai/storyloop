"""One attempt per side is a spot check (guardrail rerun 2026-11-18): recorded, never a gate."""
from pathlib import Path
import tempfile
import unittest

from storyloop.bench_run import estimate_duration, run_bench
from storyloop.cli import parser
from storyloop.models import StoryLoopError
from tests.test_bench_case import case_fixture

PASSED = {"status": "PASSED", "events": [], "calls": 1, "agent_ms": 1, "wall_ms": 2}


class BenchSpotCheckTests(unittest.TestCase):
    def _run(self, **options):
        calls = []

        def invoke(*args):
            calls.append(args[:3])
            return PASSED
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            result = run_bench([case_fixture(root)], "a" * 40, "b" * 40, "test", "fixture",
                               {"max_calls": 10}, invoke, lambda *_: True, root, scope="base-only", **options)
        return result, calls

    def test_one_attempt_per_side_runs_once_and_records_it(self):
        result, calls = self._run(attempts_per_side=1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["scoring_contract"]["attempts_per_side"], 1)
        self.assertNotIn("stage_gate", result)
        self.assertEqual(len(self._run()[1]), 2)  # the default stays two

    def test_the_cli_accepts_only_one_or_two(self):
        base = ["bench", "run", "c", "--adapter", "codex", "--model", "m", "--base-engine-commit", "x",
                "--engine-repository", ".", "--repository", "r=.", "--engine-python", "python"]
        self.assertEqual(parser().parse_args([*base, "--attempts-per-side", "1"]).attempts_per_side, 1)
        with self.assertRaises(SystemExit):
            parser().parse_args([*base, "--attempts-per-side", "3"])
        with self.assertRaises(StoryLoopError):
            self._run(attempts_per_side=3)
        self.assertEqual(estimate_duration(2, "base-only", 1), "约 1.3～2.2 小时")


if __name__ == "__main__":
    unittest.main()
