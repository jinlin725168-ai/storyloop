"""Bench execution uses disposable Git clones and test callbacks only."""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from storyloop.bench_case import load_case
from storyloop.bench_real_exec import STAGE_CASE_IDS, run_isolated_attempt, stage_gate
from storyloop.bench_worker import _execute_change, _run_until_stop
from storyloop.bench_run import compare_runs, run_bench
from storyloop.models import StoryLoopError
from tests.test_bench_calibrate import calibration_fixture


class RealBenchTests(unittest.TestCase):
    def test_isolated_base_only_passes_gold_without_touching_source(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, repository = calibration_fixture(root)
            case = load_case(source)
            before = subprocess.check_output(["git", "status", "--porcelain"], cwd=repository)
            seen = []

            def engine(tree, *_):
                seen.append(tree)
                self.assertNotEqual(tree, repository)
                self.assertEqual(subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=tree).decode().strip(),
                                 case["manifest"]["base_commit"])
                leaked = subprocess.run(["git", "cat-file", "-e",
                                         case["manifest"]["solution_commit"]], cwd=tree,
                                        capture_output=True)
                self.assertNotEqual(leaked.returncode, 0)
                (tree / "feature.py").write_text("VALUE = 2\n")
                return {"status": "PASSED", "events": [], "calls": 1, "agent_ms": 1,
                        "known_cost_usd": 0.01, "cost_coverage": 1.0}

            def invoke(side, item, attempt, human):
                return run_isolated_attempt(item, repository, "a" * 40, "test", "fixture",
                                            {"max_calls": 10}, human, engine=engine)

            result = run_bench([source], "a" * 40, "", "test", "fixture",
                               {"max_calls": 10}, invoke, None, root, scope="base-only")
            self.assertEqual(len(seen), 2)
            self.assertEqual([a["verdict"] for a in result["cases"][0]["attempts"]], ["PASSED"] * 2)
            self.assertEqual(result["suite_mode"], "generated")
            self.assertTrue(all(a["gold_passed"] and a["gold_checks"]
                                for a in result["cases"][0]["attempts"]))
            self.assertEqual(subprocess.check_output(["git", "status", "--porcelain"], cwd=repository), before)

    def test_nonroutine_gold_failure_and_budget(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, repository = calibration_fixture(root)
            case = load_case(source)
            common = (case, repository, "a" * 40, "test", "fixture", {"max_calls": 10})
            human = lambda event: "NEEDS_HUMAN" if event["kind"] == "replan" else "APPROVE"
            needs = run_isolated_attempt(*common, human,
                engine=lambda *_: {"status": "NEEDS_HUMAN", "events": [{"kind": "replan"}], "calls": 1})
            self.assertEqual(needs["status"], "NEEDS_HUMAN")
            failed = run_isolated_attempt(*common, human,
                engine=lambda *_: {"status": "PASSED", "events": [], "calls": 1})
            self.assertIs(failed["gold_passed"], False)
            budget = run_isolated_attempt(*common, human,
                engine=lambda *_: {"status": "BUDGET_EXHAUSTED", "events": [], "calls": 10})
            self.assertEqual(budget["status"], "BUDGET_EXHAUSTED")

            def verdict(adapter):
                invoke = lambda _side, item, _attempt, simulated: run_isolated_attempt(
                    item, repository, "a" * 40, "test", "fixture", {"max_calls": 10},
                    simulated, engine=adapter)
                return run_bench([source], "a" * 40, "", "test", "fixture",
                                 {"max_calls": 10}, invoke, None, root,
                                 scope="base-only")["cases"][0]["attempts"][0]

            self.assertEqual(verdict(lambda *_: {"status": "PASSED", "events": [],
                                                   "calls": 1})["verdict"], "FAILED")
            stopped = verdict(lambda *_: {"status": "NEEDS_HUMAN", "events": [{"kind": "replan"}],
                                         "calls": 1})
            self.assertEqual(stopped["verdict"], "NEEDS_HUMAN")
            self.assertEqual(stopped["nonroutine_count"], 1)
            exhausted = verdict(lambda *_: {"status": "BUDGET_EXHAUSTED", "events": [],
                                           "calls": 10})
            self.assertEqual(exhausted["verdict"], "NOT_COMPARABLE")
            self.assertEqual(exhausted["termination_reason"], "BUDGET_EXHAUSTED")

    def test_gold_rejects_candidate_link_before_copying(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, repository = calibration_fixture(root)
            case = load_case(source)
            def linked(tree, *_):
                (tree / "unsafe").symlink_to(root)
                return {"status": "PASSED", "events": [], "calls": 1}
            with self.assertRaisesRegex(StoryLoopError, "link or special file"):
                run_isolated_attempt(case, repository, "a" * 40, "test", "fixture",
                                     {"max_calls": 10}, lambda *_: "APPROVE", engine=linked)

    def test_gate_requires_four_complete_cases(self):
        cases = [{"case_id": name, "case_version": 2, "comparison": {"status": "BASE_ONLY"},
                  "attempts": [{"verdict": "PASSED", "nonroutine_count": 0},
                 {"verdict": "PASSED", "nonroutine_count": 1}]} for name in sorted(STAGE_CASE_IDS)]
        self.assertTrue(stage_gate({"case_version": 2, "scope": "base-only",
                                    "suite_mode": "generated", "cases": cases})["passed"])
        with self.assertRaises(StoryLoopError):
            stage_gate({"case_version": 2, "scope": "base-only",
                        "suite_mode": "frozen", "cases": cases})
        cases[3]["attempts"][1]["verdict"] = "NOT_COMPARABLE"
        self.assertTrue(stage_gate({"case_version": 2, "scope": "base-only",
                                    "suite_mode": "generated", "cases": cases})["passed"])
        cases[2]["attempts"][1]["nonroutine_count"] = 2
        self.assertFalse(stage_gate({"case_version": 2, "scope": "base-only",
                                     "suite_mode": "generated", "cases": cases})["passed"])
        cases[2]["attempts"][1]["nonroutine_count"] = 0
        cases[2]["comparison"] = {"status": "NOT_COMPARABLE"}
        self.assertFalse(stage_gate({"case_version": 2, "scope": "base-only",
                                     "suite_mode": "generated", "cases": cases})["passed"])
        with self.assertRaises(StoryLoopError):
            stage_gate({"case_version": 1, "scope": "base-only",
                        "suite_mode": "generated", "cases": cases})

    def test_generated_uses_normal_acceptance_and_frozen_preserves_install(self):
        contract = {"change_id": "FEAT-20261109-sample", "product": {}}
        actions = []

        def run_change(*_):
            actions.append("acceptance_role")
            return {"status": "PASSED"}

        common = dict(create_change=lambda *_args, **_kwargs: {"change_id": contract["change_id"]},
                      approve_change=lambda *_args, **_kwargs: actions.append("approve"),
                      load_change=lambda *_: {"change_id": contract["change_id"]},
                      install_smoke_suite=lambda *_: actions.append("install"),
                      run_change=run_change, approve_design=lambda *_: None,
                      load_run_state=lambda *_: {"intervention_events": []})
        generated = _execute_change(Path("/tmp/fixture"), contract, "unused", 2,
                                    "generated", **common)
        self.assertEqual(generated["status"], "PASSED")
        self.assertEqual(actions, ["approve", "acceptance_role"])
        actions.clear()
        with patch("storyloop.bench_worker._install_suite",
                   side_effect=lambda *_: actions.append("install")):
            frozen = _execute_change(Path("/tmp/fixture"), contract, "unused", 2,
                                     "frozen", **common)
        self.assertEqual(frozen["status"], "PASSED")
        self.assertEqual(actions, ["approve", "install", "acceptance_role"])

    def test_suite_mode_is_frozen_in_run_and_comparison(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, _ = calibration_fixture(root)
            outcome = {"status": "PASSED", "events": [], "calls": 1, "agent_ms": 1,
                       "wall_ms": 1, "known_cost_usd": 0.01, "cost_coverage": 1.0}
            generated = run_bench([source], "a" * 40, "", "test", "fixture",
                                  {"max_calls": 10}, lambda *_: outcome, lambda *_: True,
                                  root, scope="base-only")
            frozen = run_bench([source], "a" * 40, "", "test", "fixture",
                               {"max_calls": 10}, lambda *_: outcome, lambda *_: True,
                               root, scope="base-only", suite_mode="frozen")
            self.assertEqual(generated["suite_mode"], "generated")
            self.assertEqual(frozen["suite_mode"], "frozen")
            self.assertIn("suite_mode", compare_runs(generated, frozen)["reasons"])

    def test_simulated_human_approves_design_and_stops_on_correction(self):
        statuses = iter([{"status": "DESIGN_APPROVAL_REQUIRED"}, {"status": "PASSED"}])
        approvals = []
        run = _run_until_stop(Path("/tmp/fixture"), "FEAT-20261109-x", lambda *_: next(statuses),
                              lambda *args: approvals.append(args),
                              lambda *_: {"planning": {"plan": {"id": "plan-1"}},
                                          "intervention_events": [{"kind": "design_approval"}]})
        self.assertEqual(run["status"], "PASSED")
        self.assertEqual(approvals[0][-1], "plan-1")
        repair = _run_until_stop(Path("/tmp/fixture"), "FEAT-20261109-x",
                                 lambda *_: {"status": "ORACLE_DEFECT"}, lambda *_: None,
                                 lambda *_: {"intervention_events": []})
        self.assertEqual(repair["status"], "NEEDS_HUMAN")
        self.assertEqual(repair["events"][0]["kind"], "acceptance_repair")

    def test_cost_flag_still_requires_second_confirmation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, repository = calibration_fixture(root)
            result = subprocess.run([
                sys.executable, "-m", "storyloop.cli", "bench", "run", "example",
                "--adapter", "codex", "--model", "fixture", "--scope", "base-only",
                "--base-engine-commit", "a" * 40, "--engine-repository", str(repository),
                "--engine-python", sys.executable,
                "--repository", f"fixture={repository}", "--cases-root", str(source.parent.parent),
                "--i-accept-cost"], input="", capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn("--yes", result.stderr)
            self.assertIn("2 次", result.stderr)

    def test_default_refusal_with_complete_real_arguments(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, repository = calibration_fixture(root)
            result = subprocess.run([
                sys.executable, "-m", "storyloop.cli", "bench", "run", "example",
                "--adapter", "codex", "--model", "fixture", "--scope", "base-only",
                "--base-engine-commit", "a" * 40, "--engine-repository", str(repository),
                "--engine-python", sys.executable,
                "--repository", f"fixture={repository}", "--cases-root", str(source.parent.parent)],
                input="", capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn("真实评测默认关闭", result.stderr)


if __name__ == "__main__":
    unittest.main()
