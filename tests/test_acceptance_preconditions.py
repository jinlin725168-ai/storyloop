"""Fixture mistakes cannot prove RED and their stop shows bounded evidence."""

import json
from pathlib import Path
import re
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from storyloop.changes import load_change
from storyloop.cli import _error_stop
from storyloop.models import StoryLoopError
from storyloop.smoke_runner import _judge_red, _run_test
from storyloop.stop_report import build_stop
from tests.support import product
from tests.test_smoke_harness import fixture


def _test(case: str) -> dict:
    return {"id": "SMOKE-1", "criteria": ["AC-1"], "timeout_seconds": 15,
            "command": ["{storyloop_harness}", "acceptance/tools/python_smoke.py", case]}


class AcceptancePreconditionTests(unittest.TestCase):
    def test_command_case_precondition_is_repairable_oracle_defect(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            case = root / "case.py"
            case.write_text(
                "import sys, unittest\n"
                f"sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})\n"
                "from storyloop.testing.smoke_harness.v1 import precondition\n"
                "class Case(unittest.TestCase):\n"
                "    def test_fixture(self):\n"
                "        precondition(False, 'missing fixture root')\n"
                "unittest.main()\n", encoding="utf-8")
            test = {**_test("case.py"), "command": [sys.executable, "case.py"]}
            result = _run_test(root, test, [])
            self.assertEqual(result["status"], "ERROR")
            self.assertEqual(result["counts"]["diagnostics"], ["precondition"])
            with self.assertRaises(StoryLoopError) as caught:
                _judge_red([result], {"tests": [test]}, {"product": product()})
            self.assertEqual(caught.exception.code, "ORACLE_DEFECT")
            self.assertIn("fixture precondition", str(caught.exception))
            stop = build_stop(root, {"change_id": "FEAT-fixture", "status": "CONTRACT_COMMITTED"},
                              "ORACLE_DEFECT", {"acceptance": caught.exception.details})
            self.assertIn("missing fixture root", "\n".join(stop["evidence"]))
            self.assertTrue(any("acceptance-repair" in option["command"] for option in stop["options"]))

    def test_process_timeout_still_dominates_command_precondition_marker(self):
        test = {**_test("case.py"), "command": [sys.executable, "case.py"]}
        outcome = {"contained": True, "timed_out": True, "returncode": 1,
                   "stdout": "Ran 1 test in 0.01s\n\nFAILED (errors=1)",
                   "stderr": "STORYLOOP_PRECONDITION_FAILED: fixture unavailable\n"}
        with patch("storyloop.smoke_runner.run_process", return_value=outcome):
            result = _run_test(Path("/tmp"), test, [])
        with self.assertRaises(StoryLoopError) as caught:
            _judge_red([result], {"tests": [test]}, {"product": product()})
        self.assertEqual(caught.exception.code, "BLOCKED")

    def test_command_case_precondition_with_error_exit_two_is_repairable(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "case.py").write_text(
                "import sys, unittest\n"
                f"sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})\n"
                "from storyloop.testing.smoke_harness.v1 import precondition\n"
                "class Case(unittest.TestCase):\n"
                "    def test_fixture(self):\n"
                "        precondition(False, 'exit two fixture marker')\n"
                "outcome = unittest.main(exit=False)\n"
                "sys.exit(2 if outcome.result.errors else 1 if outcome.result.failures else 0)\n",
                encoding="utf-8")
            test = {**_test("case.py"), "command": [sys.executable, "case.py"]}
            result = _run_test(root, test, [])
            self.assertEqual(result["returncode"], 2)
            with self.assertRaises(StoryLoopError) as caught:
                _judge_red([result], {"tests": [test]}, {"product": product()})
            self.assertEqual(caught.exception.code, "ORACLE_DEFECT")
            self.assertEqual(result["counts"]["diagnostics"], ["precondition"])

    def test_failed_precondition_is_oracle_defect_with_output_in_stop(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fixture(root)
            (root / "cases/precondition.py").write_text(
                "import unittest\nfrom storyloop.testing.smoke_harness.v1 import precondition\n"
                "class Case(unittest.TestCase):\n"
                "    def test_fixture(self):\n"
                "        precondition(False, 'fixture marker: test_roots missing')\n",
                encoding="utf-8")
            test = _test("cases/precondition.py")
            result = _run_test(root, test, [])
            self.assertEqual(result["status"], "ERROR")
            self.assertEqual(result["counts"]["failed"], 0)
            with self.assertRaises(StoryLoopError) as caught:
                _judge_red([result], {"tests": [test]}, {"product": product()})
            self.assertEqual(caught.exception.code, "ORACLE_DEFECT")
            stop = build_stop(root, {"change_id": "FEAT-fixture", "status": "CONTRACT_COMMITTED"},
                              "ORACLE_DEFECT", {"acceptance": caught.exception.details})
            self.assertIn("fixture marker: test_roots missing", "\n".join(stop["evidence"]))
            self.assertLessEqual(max(map(len, stop["evidence"])), 1500)

    def test_real_assertion_failure_still_proves_red(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fixture(root)
            test = _test("cases/b.py")
            result = _run_test(root, test, [])
            self.assertEqual(result["status"], "FAIL")
            _judge_red([result], {"tests": [test]}, {"product": product()})

    def test_unavailable_command_remains_environment_error(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            test = {**_test("cases/b.py"), "command": ["storyloop-command-that-does-not-exist"]}
            with self.assertRaises(StoryLoopError) as caught:
                _run_test(root, test, [])
            self.assertEqual(caught.exception.code, "BLOCKED")

    def test_fixture_error_stop_shows_failed_case_output(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            test = {**_test("cases/b.py"),
                    "command": ["{python}", "-c", "import sys; print('environment marker'); sys.exit(2)"]}
            result = _run_test(root, test, [])
            with self.assertRaises(StoryLoopError) as caught:
                _judge_red([result], {"tests": [test]}, {"product": product()})
            self.assertEqual(caught.exception.code, "ORACLE_DEFECT")
            self.assertTrue(caught.exception.details["fixture_repairable"])
            stop = build_stop(root, {"change_id": "FEAT-fixture", "status": "CONTRACT_COMMITTED"},
                              "ORACLE_DEFECT", {"acceptance": caught.exception.details})
            self.assertIn("environment marker", "\n".join(stop["evidence"]))

    def test_environment_error_still_dominates_a_fixture_precondition(self):
        test = _test("cases/precondition.py")
        result = {"id": "SMOKE-1", "criteria": ["AC-1"], "status": "ERROR", "stdout": "",
                  "stderr": "fixture and cleanup failed",
                  "counts": {"executed": 1, "failed": 0, "errors": 2,
                             "diagnostics": ["precondition", "cleanup"]}}
        with self.assertRaises(StoryLoopError) as caught:
            _judge_red([result], {"tests": [test]}, {"product": product()})
        self.assertEqual(caught.exception.code, "BLOCKED")

    def test_environment_error_in_another_case_dominates_precondition(self):
        tests = [_test("cases/precondition.py"), {**_test("cases/error.py"), "id": "SMOKE-2"}]
        results = [{"id": test["id"], "criteria": ["AC-1"], "status": "ERROR",
                    "counts": {"executed": 1, "failed": 0, "errors": 1,
                               "diagnostics": ["precondition"] if index == 0 else ["runner"]}}
                   for index, test in enumerate(tests)]
        with self.assertRaises(StoryLoopError) as caught:
            _judge_red(results, {"tests": tests}, {"product": product()})
        self.assertEqual(caught.exception.code, "BLOCKED")

    def test_all_executed_unittest_errors_are_repairable_fixture_defects(self):
        test = _test("cases/b.py")
        result = {"id": "SMOKE-1", "criteria": ["AC-1"], "status": "ERROR",
                  "returncode": 2, "timed_out": False, "stdout": "", "stderr": "ModuleNotFoundError: fixture",
                  "counts": {"executed": 1, "failed": 0, "errors": 1, "parser": "unittest"}}
        with self.assertRaises(StoryLoopError) as caught:
            _judge_red([result], {"tests": [test]}, {"product": product()})
        self.assertEqual(caught.exception.code, "ORACLE_DEFECT")
        self.assertTrue(caught.exception.details["fixture_repairable"])

    def test_truncated_unittest_error_stays_an_environment_error(self):
        test = {**_test("case.py"), "command": [sys.executable, "case.py"]}
        outcome = {"contained": True, "truncated": True, "returncode": 2,
                   "stdout": "Ran 1 test in 0.01s\n\nFAILED (errors=1)",
                   "stderr": "ModuleNotFoundError: fixture"}
        with patch("storyloop.smoke_runner.run_process", return_value=outcome):
            result = _run_test(Path("/tmp"), test, [])
        with self.assertRaises(StoryLoopError) as caught:
            _judge_red([result], {"tests": [test]}, {"product": product()})
        self.assertEqual(caught.exception.code, "BLOCKED")

    def test_cli_environment_error_stop_uses_runner_output(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / ".storyloop/state/runs/FEAT-fixture.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"change_id": "FEAT-fixture", "status": "CONTRACT_COMMITTED"}),
                            encoding="utf-8")
            error = StoryLoopError("Smoke RED proof encountered an environment error", details={
                "results": [{"id": "SMOKE-1", "status": "ERROR", "stderr": "environment marker"}]})
            stop = _error_stop(SimpleNamespace(project=root, change_id="FEAT-fixture", command="run"), error)
            self.assertIn("environment marker", "\n".join(stop["evidence"]))

    def test_repair_error_stop_shows_bounded_red_output(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / ".storyloop/state/runs/FEAT-fixture.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"change_id": "FEAT-fixture", "status": "ORACLE_DEFECT"}),
                            encoding="utf-8")
            for code in ("BLOCKED", "ORACLE_DEFECT"):
                with self.subTest(code=code):
                    error = StoryLoopError("RED reproof failed", code=code, details={
                        "results": [{"id": "SMOKE-1", "status": "ERROR",
                                     "stderr": "x" * 2000 + " repair output marker"}]})
                    stop = _error_stop(SimpleNamespace(project=root, change_id="FEAT-fixture",
                                                       command="acceptance-repair"), error)
                    self.assertIn("repair output marker", "\n".join(stop["evidence"]))
                    self.assertLessEqual(max(map(len, stop["evidence"])), 1500)

    def test_stop_evidence_keeps_case_id_with_bounded_output(self):
        output = "x" * 2000 + " final marker"
        stop = build_stop(Path("/tmp"), {"change_id": "FEAT-fixture", "status": "CONTRACT_COMMITTED"},
                          "ORACLE_DEFECT", {"acceptance": {"results": [
                              {"id": "SMOKE-1", "status": "ERROR", "stderr": output}]}})
        self.assertEqual(len(stop["evidence"]), 1)
        self.assertTrue(stop["evidence"][0].startswith("SMOKE-1 ERROR: "))
        self.assertTrue(stop["evidence"][0].endswith(" final marker"))
        self.assertLessEqual(len(stop["evidence"][0]), 1500)

    def test_guide_change_fixture_is_accepted_by_load_change(self):
        guide = (Path(__file__).resolve().parents[1] / "acceptance/AGENT_GUIDE.md").read_text(encoding="utf-8")
        match = re.search(r"```json storyloop-change-fixture\n(.*?)\n```", guide, re.DOTALL)
        self.assertIsNotNone(match)
        value = json.loads(match.group(1))
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / ".storyloop/state/changes" / f"{value['change_id']}.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(value), encoding="utf-8")
            self.assertEqual(load_change(root, value["change_id"]), value)
