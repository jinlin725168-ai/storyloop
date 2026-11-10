"""The bench scheduler is tested with a stub, never a real agent."""

from pathlib import Path
import tempfile
import unittest

from storyloop.bench_run import run_bench, compare_runs
from tests.test_bench_case import case_fixture, stale_approval_fixture, v2_case_fixture
from tests.test_bench_calibrate import calibration_fixture
from storyloop.models import StoryLoopError


class BenchRunTests(unittest.TestCase):
    def test_modified_approved_v2_contract_is_rejected_before_adapter(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = stale_approval_fixture(v2_case_fixture(case_fixture(root), approved=True))
            calls = []
            with self.assertRaises(StoryLoopError):
                run_bench([source], "a" * 40, "b" * 40, "test", "fixture",
                          {"max_calls": 10}, lambda *_: calls.append(1), lambda *_: True, root)
            self.assertEqual(calls, [])

    def test_unapproved_v2_is_rejected_before_invoking_adapter(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = v2_case_fixture(case_fixture(root))
            calls = []
            with self.assertRaises(StoryLoopError):
                run_bench([source], "a" * 40, "b" * 40, "test", "fixture",
                          {"max_calls": 10}, lambda *_: calls.append(1), lambda *_: True, root)
            self.assertEqual(calls, [])

    def test_approved_v2_runs_with_test_adapter(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = v2_case_fixture(case_fixture(root), approved=True)
            outcome = {"status": "PASSED", "events": [], "calls": 1, "agent_ms": 1,
                       "wall_ms": 2, "known_cost_usd": 0.01, "cost_coverage": 1.0}
            result = run_bench([source], "a" * 40, "b" * 40, "test", "fixture",
                               {"max_calls": 10}, lambda *_: outcome, lambda *_: True, root)
            self.assertEqual(result["case_version"], 2)
            self.assertEqual(len(result["cases"][0]["attempts"]), 4)

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
            self.assertEqual(attempts[1]["verdict"], "PASSED")
            self.assertEqual(attempts[1]["nonroutine_types"], {"replan": 1})
            self.assertEqual(attempts[0]["verdict"], "PASSED")

    def test_missing_cost_does_not_block_outcome_comparison(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = case_fixture(root)
            adapter = lambda *_: {"status": "PASSED", "events": [], "calls": 1,
                                  "agent_ms": 1, "wall_ms": 2,
                                  "known_cost_usd": None, "cost_coverage": None}
            result = run_bench([source], "a" * 40, "b" * 40, "test", "fixture",
                               {"max_calls": 10}, adapter, lambda *_: True, root)
            self.assertEqual(result["scoring_contract"]["version"], 3)
            self.assertEqual(result["cases"][0]["comparison"]["status"], "COMPARABLE")
            self.assertEqual(result["cases"][0]["comparison"]["cost_dimension"], "NOT_COMPARABLE")
            self.assertEqual(result["cases"][0]["attempts"][0]["cost_accounting"], "UNAVAILABLE")
            other = {**result, "model": "different"}
            self.assertEqual(compare_runs(result, other)["status"], "NOT_COMPARABLE")
            self.assertIn("model", compare_runs(result, other)["reasons"])
            other = {**result, "case_version": 2}
            self.assertIn("case_version", compare_runs(result, other)["reasons"])
            other = {**result, "scope": "base-only"}
            self.assertIn("scope", compare_runs(result, other)["reasons"])

    def test_readiness_evidence_survives_attempt_record(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = case_fixture(root)
            evidence = {"status": "PROJECT_NOT_READY", "blockers": ["unit"], "layers": {}}
            outcome = {"status": "NOT_COMPARABLE", "reason": "readiness:PROJECT_NOT_READY",
                       "events": [], "calls": 0, "agent_ms": 0, "wall_ms": 3,
                       "readiness": evidence, "readiness_evidence_path": ".storyloop/bench/evidence/x/readiness.json"}
            result = run_bench([source], "a" * 40, "", "test", "fixture",
                               {"max_calls": 10}, lambda *_: outcome, lambda *_: True,
                               root, scope="base-only")
            attempt = result["cases"][0]["attempts"][0]
            self.assertEqual(attempt["verdict"], "NOT_COMPARABLE")
            self.assertEqual(attempt["readiness"], evidence)
            self.assertEqual(attempt["readiness_evidence_path"], outcome["readiness_evidence_path"])

    def test_partial_cost_does_not_block_but_missing_calls_does(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = case_fixture(root)
            base = {"status": "PASSED", "events": [], "calls": 1, "agent_ms": 1,
                    "wall_ms": 2, "known_cost_usd": 0.1, "cost_coverage": 0.5}
            result = run_bench([source], "a" * 40, "", "test", "fixture",
                               {"max_calls": 10}, lambda *_: base, lambda *_: True,
                               root, scope="base-only")
            self.assertEqual(result["cases"][0]["comparison"]["status"], "BASE_ONLY")
            self.assertEqual(result["cases"][0]["attempts"][0]["cost_accounting"], "PARTIAL")
            missing = {**base, "calls": None}
            result = run_bench([source], "a" * 40, "", "test", "fixture",
                               {"max_calls": 10}, lambda *_: missing, lambda *_: True,
                               root, scope="base-only")
            self.assertIn("calls", result["cases"][0]["comparison"]["reasons"])

    def test_real_adapter_requires_explicit_cost_confirmation(self):
        with tempfile.TemporaryDirectory() as folder:
            source = case_fixture(Path(folder))
            with self.assertRaises(StoryLoopError):
                run_bench([source], "a" * 40, "b" * 40, "codex", "configured",
                          {"max_calls": 10}, lambda *_: {}, lambda *_: True, Path(folder))

    def test_default_hidden_gold_judges_candidate_tree(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, repository = calibration_fixture(root)

            def adapter(*_):
                return {"status": "PASSED", "events": [], "calls": 1, "agent_ms": 1,
                        "wall_ms": 2, "known_cost_usd": 0.01, "cost_coverage": 1.0,
                        "tree": str(repository)}

            result = run_bench([source], "a" * 40, "b" * 40, "test", "fixture",
                               {"max_calls": 10}, adapter, None, root)
            self.assertEqual(result["cases"][0]["attempts"][0]["verdict"], "PASSED")
            changed = {**result, "gold_python": {**result["gold_python"], "path": "/other/python"}}
            self.assertIn("gold_python", compare_runs(result, changed)["reasons"])

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
