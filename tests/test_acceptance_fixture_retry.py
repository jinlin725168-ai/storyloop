"""Fixture errors in RED may be repaired by acceptance without a human round trip."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from storyloop.acceptance_repair import repair_smoke
from storyloop.approvals import approve_change
from storyloop.baseline import bootstrap_project
from storyloop.project_loop import load_run_state, run_change
from storyloop.stop_report import build_stop
from storyloop.models import StoryLoopError
from storyloop.smoke_runner import _judge_red
from tests.routing_support import legacy_create_change
from tests.support import product
from tests.test_baseline_behavior import git, repository
from tests.test_smoke_repair import services, self_writing_payload
from tests.test_project_loop import smoke_payload


def fixture_error(change_id: str, marker: str) -> dict:
    payload = smoke_payload(change_id)
    payload["files"][0]["content"] = (
        "import unittest\n"
        "class Case(unittest.TestCase):\n"
        "    def test_fixture(self):\n"
        f"        raise ModuleNotFoundError({marker!r})\n"
        "unittest.main()\n"
    )
    return payload


def precondition_error(change_id: str) -> dict:
    payload = smoke_payload(change_id)
    payload["files"][0]["content"] = (
        "import unittest\n"
        "from storyloop.testing.smoke_harness.v1 import precondition\n"
        "class Case(unittest.TestCase):\n"
        "    def test_fixture(self):\n"
        "        precondition(False, 'missing fixture')\n"
        "unittest.main()\n"
    )
    return payload


def precondition_marker_error(change_id: str) -> dict:
    """Exercise RED's public marker protocol without depending on an installed package."""
    payload = smoke_payload(change_id)
    payload["files"][0]["content"] = (
        "import sys, unittest\n"
        "class Case(unittest.TestCase):\n"
        "    def test_fixture(self):\n"
        "        print('STORYLOOP_PRECONDITION_FAILED: missing fixture', file=sys.stderr)\n"
        "        raise RuntimeError('missing fixture')\n"
        "unittest.main()\n"
    )
    return payload


class AcceptanceFixtureRetryTests(unittest.TestCase):
    def approved(self, root: Path, overrides: dict | None = None,
                 contract: dict | None = None) -> str:
        repository(root)
        bootstrap_project(root, {"test_commands": [], **(overrides or {})})
        change_id = legacy_create_change(root, "orders", contract or product())["change_id"]
        approve_change(root, change_id)
        return change_id

    def test_must_hold_red_is_rewritten_automatically(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            contract = product()
            contract["stories"][0]["acceptance"][0]["row_type"] = "regression"
            change_id = self.approved(root, contract=contract)
            calls = []

            def generate(project, request):
                calls.append(request)
                answer = smoke_payload(change_id)
                if len(calls) > 1:
                    answer["files"][0]["content"] = "raise SystemExit(0)\n"
                return answer

            result = run_change(root, change_id, services(change_id, generate))
            self.assertEqual(result["status"], "PASSED", result)
            state = load_run_state(root, change_id)
            self.assertEqual(len(state["repairs"]), 1)
            self.assertTrue(state["repairs"][0]["automatic"])
            self.assertEqual(calls[1]["repair"]["failure_evidence"]["defect_category"],
                             "MUST_HOLD_RED")
            self.assertEqual(calls[1]["repair"]["failure_evidence"]["diagnostics"][0]["case_id"],
                             "SMOKE-1")
            self.assertEqual(state["intervention_events"][-1]["automatic"], True)

    def test_third_must_hold_red_stops_after_two_automatic_rewrites(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            contract = product()
            contract["stories"][0]["acceptance"][0]["row_type"] = "regression"
            change_id = self.approved(root, contract=contract)
            calls = []

            def generate(project, request):
                calls.append(request)
                answer = smoke_payload(change_id)
                answer["files"][0]["content"] += f"# retry {len(calls)}\n"
                return answer

            result = run_change(root, change_id, services(change_id, generate))
            self.assertEqual(result["status"], "ORACLE_DEFECT", result)
            state = load_run_state(root, change_id)
            self.assertEqual(len(calls), 3)
            self.assertEqual(sum(r.get("automatic") is True for r in state["repairs"]), 2)
            self.assertTrue(all(event.get("automatic") is True for event in
                                state["intervention_events"] if event["kind"] == "acceptance_repair"))

    def test_case_root_violation_is_returned_to_acceptance_automatically(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            change_id = self.approved(root)
            calls = []

            def generate(project, request):
                calls.append(request)
                answer = smoke_payload(change_id)
                answer["files"][0]["content"] += f"# revision {len(calls)}\n"
                return answer

            diagnostic = {"case_id": "launcher", "kind": "error",
                          "message": "StoryLoopError: Case is outside declared roots",
                          "traceback_tail": ""}
            evidence = {"fixture_repairable": True,
                        "defect_category": "MANIFEST_OR_SOURCE_BOUNDARY",
                        "test_ids": ["SMOKE-1"], "diagnostics": [diagnostic],
                        "results": [{"id": "SMOKE-1", "status": "ERROR",
                                     "diagnostics": ["launch", diagnostic]}]}
            error = StoryLoopError("Case is outside declared roots", code="ORACLE_DEFECT",
                                   details=evidence)
            with patch("storyloop.project_preparation._ensure_red_evidence", side_effect=error):
                result = run_change(root, change_id, services(change_id, generate))
            self.assertEqual(result["status"], "PASSED", result)
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[1]["repair"]["failure_evidence"]["defect_category"],
                             "MANIFEST_OR_SOURCE_BOUNDARY")
            self.assertEqual(calls[1]["repair"]["failure_evidence"]["diagnostics"][0]["case_id"],
                             "launcher")
            self.assertTrue(load_run_state(root, change_id)["repairs"][0]["automatic"])

    def test_missing_source_module_is_a_repairable_suite_boundary(self):
        contract = product()
        manifest = {"tests": [{"id": "T-1", "criteria": ["AC-1"]}]}
        row = {"id": "T-1", "criteria": ["AC-1"], "status": "ERROR", "returncode": 1,
               "timed_out": False, "stdout": "", "stderr": "",
               "counts": {"executed": 1, "failed": 0, "errors": 1,
                          "diagnostics": [{"case_id": "Case.test_one", "kind": "error",
                                           "message": "ModuleNotFoundError: No module named 'probe'",
                                           "traceback_tail": "acceptance/changes/C/test.py:5"}]}}
        with self.assertRaises(StoryLoopError) as caught:
            _judge_red([row], manifest, {"product": contract})
        self.assertEqual(caught.exception.details["defect_category"], "MANIFEST_OR_SOURCE_BOUNDARY")
        self.assertTrue(caught.exception.details["fixture_repairable"])
        row["counts"]["diagnostics"] = []
        row["stderr"] = "ModuleNotFoundError: No module named 'probe'"
        with self.assertRaises(StoryLoopError) as caught:
            _judge_red([row], manifest, {"product": contract})
        self.assertEqual(caught.exception.details["defect_category"], "MANIFEST_OR_SOURCE_BOUNDARY")

    def test_uninvoked_entry_is_returned_to_acceptance_automatically(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            contract = product()
            contract["stories"][0]["verification_entries"] = [{
                "id": "VE-STATUS", "kind": "cli", "locator": "storyloop status <project> --json",
                "observable": "status JSON", "reason": "public command"}]
            change_id = self.approved(root, contract=contract)
            calls = []

            def generate(project, request):
                calls.append(request)
                answer = smoke_payload(change_id)
                answer["manifest"]["tests"][0]["entry_ids"] = ["VE-STATUS"]
                if len(calls) > 1:
                    answer["files"][0]["content"] = (
                        "import subprocess, sys\n"
                        "subprocess.run([sys.executable, '-m', 'storyloop', 'status', '.', "
                        "'--change', 'C', '--json'], capture_output=True)\n"
                        "raise SystemExit(1)\n")
                return answer

            run_change(root, change_id, services(change_id, generate))
            state = load_run_state(root, change_id)
            self.assertGreaterEqual(len(calls), 2)
            self.assertEqual(calls[1]["repair"]["failure_evidence"]["defect_category"],
                             "UNVERIFIED_ENTRY_USE")
            self.assertEqual(calls[1]["repair"]["failure_evidence"]["entries"][0]["id"],
                             "VE-STATUS")
            entry_use = calls[1]["repair"]["failure_evidence"]["entry_use_findings"][0]
            self.assertEqual(entry_use["entry_kind"], "cli")
            self.assertIn("storyloop status", entry_use["entry_locator"])
            self.assertIn("subprocess", entry_use["required_call"])
            self.assertTrue(entry_use["path"].endswith(".py"))
            self.assertGreater(entry_use["line"], 0)
            self.assertTrue(state["repairs"][0]["automatic"])

    def test_approve_repairs_two_fixture_errors_then_proves_red(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            change_id = self.approved(root)
            calls = []

            def generate(project, request):
                calls.append(request)
                if len(calls) < 3:
                    return fixture_error(change_id, f"fixture error {len(calls)}")
                return smoke_payload(change_id)

            result = run_change(root, change_id, services(change_id, generate))
            self.assertEqual(result["status"], "PASSED", result)
            self.assertEqual(len(calls), 3)
            self.assertIn("fixture error 1", str(calls[1]["repair"]["failure_evidence"]))
            state = load_run_state(root, change_id)
            self.assertEqual(len(state["repairs"]), 2)
            self.assertTrue(all(item["automatic"] for item in state["repairs"]))
            self.assertTrue(all(item["reason"] == "RED 证明中夹具/前提失败" for item in state["repairs"]))

    def test_approve_repairs_precondition_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            change_id = self.approved(root)
            calls = []

            def generate(project, request):
                calls.append(request)
                return precondition_marker_error(change_id) if len(calls) == 1 else smoke_payload(change_id)

            result = run_change(root, change_id, services(change_id, generate))
            self.assertEqual(result["status"], "PASSED", result)
            self.assertIn("missing fixture", str(calls[1]["repair"]["failure_evidence"]))
            self.assertTrue(load_run_state(root, change_id)["repairs"][0]["automatic"])

    def test_shadowed_self_helper_is_repaired_once_before_red(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            old_helper = root / "storyloop/testing/smoke_harness/v1.py"
            old_helper.parent.mkdir(parents=True)
            old_helper.write_text("def precondition(ok, message):\n    if not ok: raise RuntimeError(message)\n",
                                  encoding="utf-8")
            git(root, "add", "storyloop/testing/smoke_harness/v1.py")
            git(root, "commit", "-q", "-m", "old candidate helper")
            bootstrap_project(root, {"test_commands": []})
            change_id = legacy_create_change(root, "orders", product())["change_id"]
            approve_change(root, change_id)
            calls = []

            def generate(project, request):
                calls.append(request)
                return precondition_error(change_id) if len(calls) == 1 else smoke_payload(change_id)

            result = run_change(root, change_id, services(change_id, generate))
            self.assertEqual(result["status"], "PASSED", result)
            self.assertEqual(len(calls), 2)
            evidence = calls[1]["repair"]["failure_evidence"]
            self.assertEqual(evidence["reason"], "HARNESS_SHADOWED")
            self.assertIn("flow.py", evidence["files"][0])
            self.assertIn("{storyloop_harness}", evidence["repair"])
            self.assertTrue(load_run_state(root, change_id)["repairs"][0]["automatic"])

    def test_approve_stops_after_configured_retry_limit_with_each_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            change_id = self.approved(root, {"acceptance_fixture_retry_limit": 1})
            calls = []

            def generate(project, request):
                calls.append(request)
                return fixture_error(change_id, f"fixture error {len(calls)}")

            result = run_change(root, change_id, services(change_id, generate))
            self.assertEqual(result["status"], "ORACLE_DEFECT", result)
            self.assertEqual(len(calls), 2)
            self.assertEqual(len(load_run_state(root, change_id)["repairs"]), 1)
            self.assertIn("fixture error 2", "\n".join(result["stop"]["evidence"]))
            self.assertIn("自动修复 1", "\n".join(result["stop"]["evidence"]))

    def test_default_two_failed_repairs_are_both_in_stop_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            change_id = self.approved(root)
            calls = []

            def generate(project, request):
                calls.append(request)
                return fixture_error(change_id, f"failed fixture {len(calls)}")

            result = run_change(root, change_id, services(change_id, generate))
            self.assertEqual(result["status"], "ORACLE_DEFECT", result)
            self.assertEqual(len(calls), 3)
            evidence = "\n".join(result["stop"]["evidence"])
            self.assertIn("自动修复 1", evidence)
            self.assertIn("failed fixture 2", evidence)
            self.assertIn("自动修复 2", evidence)
            self.assertIn("failed fixture 3", evidence)

    def test_zero_config_disables_automatic_repair(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            change_id = self.approved(root, {"acceptance_fixture_retry_limit": 0})
            calls = []

            def generate(project, request):
                calls.append(request)
                return fixture_error(change_id, "fixture issue")

            result = run_change(root, change_id, services(change_id, generate))
            self.assertEqual(result["status"], "ORACLE_DEFECT", result)
            self.assertEqual(len(calls), 1)
            self.assertNotIn("repairs", load_run_state(root, change_id))

    def test_old_fixture_failure_summaries_do_not_leak_into_unrelated_stops(self):
        state = {"change_id": "FEAT-fixture", "status": "NEEDS_INPUT",
                 "repairs": [{"automatic": True, "failure_summary": "old fixture failure"}]}
        stop = build_stop(Path("/tmp"), state, "NEEDS_INPUT", {"questions": ["new question"]})
        self.assertNotIn("old fixture failure", "\n".join(stop["evidence"]))

    def test_manual_repair_retries_fixture_error_once(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            change_id = self.approved(root)
            with self.assertRaisesRegex(Exception, "Protected smoke suite was modified"):
                run_change(root, change_id,
                           services(change_id, lambda p, r: self_writing_payload(change_id)))
            calls = []

            def generate(project, request):
                calls.append(request)
                return (fixture_error(change_id, "repair fixture error") if len(calls) == 1
                        else smoke_payload(change_id))

            result = repair_smoke(root, change_id, "人工修复原套件", services(change_id, generate))
            self.assertEqual(result["status"], "ACCEPTANCE_REPAIRED", result)
            self.assertEqual(len(calls), 2)
            repairs = load_run_state(root, change_id)["repairs"]
            self.assertEqual([item.get("automatic", False) for item in repairs], [False, True])
