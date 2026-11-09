"""Fixture errors in RED may be repaired by acceptance without a human round trip."""

from pathlib import Path
import tempfile
import unittest

from storyloop.acceptance_repair import repair_smoke
from storyloop.approvals import approve_change
from storyloop.baseline import bootstrap_project
from storyloop.project_loop import load_run_state, run_change
from storyloop.stop_report import build_stop
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


class AcceptanceFixtureRetryTests(unittest.TestCase):
    def approved(self, root: Path, overrides: dict | None = None) -> str:
        repository(root)
        bootstrap_project(root, {"test_commands": [], **(overrides or {})})
        change_id = legacy_create_change(root, "orders", product())["change_id"]
        approve_change(root, change_id)
        return change_id

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
                return precondition_error(change_id) if len(calls) == 1 else smoke_payload(change_id)

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
