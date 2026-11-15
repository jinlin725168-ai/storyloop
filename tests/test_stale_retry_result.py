"""A repaired suite, rather than the preceding stop, determines retry and resume."""

from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch


def blocked(digest: str, test_line: int) -> dict:
    return {"status": "ORACLE_DEFECT", "acceptance": {
        "status": "ORACLE_DEFECT", "fixture_repairable": True,
        "defect_category": "UNVERIFIED_ENTRY_USE", "suite_digest": digest,
        "entry_use_findings": [{"test_id": "T", "path": "test.py", "line": test_line,
                                "entry_id": "VE-1", "criterion_ids": ["AC-1"]}]}}


class StaleRetryResultTests(unittest.TestCase):
    def test_two_rewrites_use_latest_suite_and_prove_red(self):
        from storyloop.approvals import approve_change
        from storyloop.baseline import bootstrap_project
        from storyloop.project_loop import load_run_state, run_change
        from storyloop.run_services import LoopServices
        from tests.routing_support import legacy_create_change
        from tests.support import product
        from tests.test_baseline_behavior import repository
        from tests.test_project_loop import smoke_payload
        from tests.test_oracle_strength_precheck import finding

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            bootstrap_project(root, {"test_commands": []})
            contract = product()
            contract["stories"][0]["verification_entries"] = [{
                "id": "VE-STATUS", "kind": "cli", "locator": "storyloop status <project> --json",
                "observable": "status JSON", "reason": "public command"}]
            contract["stories"][0]["acceptance"][0]["entry_ids"] = ["VE-STATUS"]
            change_id = legacy_create_change(root, "orders", contract)["change_id"]
            approve_change(root, change_id)
            generated = []
            reviews = []

            def smoke(_project, _request):
                generated.append(1)
                payload = smoke_payload(change_id)
                payload["manifest"]["tests"][0]["entry_ids"] = ["VE-STATUS"]
                if len(generated) == 3:
                    payload["files"][0]["content"] = (
                        "import subprocess, sys\n"
                        "subprocess.run([sys.executable, '-m', 'storyloop', 'status', '.', "
                        "'--json'], capture_output=True)\n" + payload["files"][0]["content"])
                payload["files"][0]["content"] += f"# suite {len(generated)}\n"
                return payload

            def review(_project, context):
                if context.get("mode") == "oracle-strength":
                    reviews.append(1)
                    if len(reviews) == 1:
                        return {"verdict": "fail", "summary": "weak", "findings": [finding("MUST_FIX")],
                                "questions": []}
                return {"verdict": "pass", "summary": "ok", "findings": [], "questions": []}

            def implement(project, _context):
                path = project / "modules/orders/service.py"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("VALUE = 1\n")
                return {"summary": "done", "questions": []}

            service = LoopServices(generate_smoke=smoke, review=review, implement=implement)
            result = run_change(root, change_id, service)
            state = load_run_state(root, change_id)
            self.assertEqual(result["status"], "PASSED", result)
            self.assertEqual(len(generated), 3)
            self.assertEqual(len(state["repairs"]), 2)
            self.assertTrue(state["red_evidence_sha256"])
            self.assertNotIn("entry_use_findings", state.get("acceptance") or {})

    def test_second_repair_proves_red_even_if_old_failure_result_is_returned(self):
        from storyloop.acceptance_repair import retry_fixture_red

        first = {"status": "ORACLE_DEFECT", "acceptance": {
            "status": "ORACLE_DEFECT", "fixture_repairable": True,
            "defect_category": "ORACLE_STRENGTH_PRECHECK", "suite_digest": "suite-0",
            "precheck": {"findings": [{"id": "ORACLE-AC-1-01", "criterion_id": "AC-1",
                                       "location": "acceptance/test.py:62", "blocking": True}]}}}
        previous = blocked("suite-1", 68)
        state = {"change_id": "C", "status": "ORACLE_DEFECT", "acceptance": first["acceptance"],
                 "repairs": []}
        calls = []

        def repair(*_args, **_kwargs):
            calls.append(1)
            state["repairs"].append({"automatic": True})
            if len(calls) == 1:
                state["acceptance"] = previous["acceptance"]
                return previous
            if len(calls) == 2:
                state["status"] = "IMPLEMENTING"
                state.pop("acceptance")
                state["red_evidence_sha256"] = "red-current"
                return previous  # stale result from the first failed repair
            raise AssertionError("a third repair would use obsolete evidence")

        with patch("storyloop.acceptance_repair.load_project_config", return_value={}), \
             patch("storyloop.acceptance_repair.load_json", return_value=state), \
             patch("storyloop.acceptance_repair.save_run"), \
             patch("storyloop.acceptance_repair.pause", return_value={"status": "ORACLE_DEFECT"}), \
             patch("storyloop.acceptance_repair._repair_once", side_effect=repair):
            result = retry_fixture_red(Path("/tmp"), "C", None, first)
        self.assertEqual(result["status"], "ACCEPTANCE_REPAIRED")
        self.assertEqual(len(calls), 2)

    def test_stale_stop_digest_requires_recheck_on_run(self):
        from storyloop.acceptance_progress import stale_acceptance_stop

        state = {"status": "ORACLE_DEFECT", "acceptance": {
            **blocked("suite-1", 68)["acceptance"]},
            "repairs": [{"automatic": True}, {"automatic": True}]}
        self.assertTrue(stale_acceptance_stop(state, "suite-2"))
        self.assertFalse(stale_acceptance_stop(state, "suite-1"))
        state["acceptance"].pop("suite_digest")
        state["acceptance_retry"] = {"rounds": [{"repair_count": 1}]}
        self.assertTrue(stale_acceptance_stop(state, "suite-2"))

    def test_retry_uses_current_stop_when_returned_failure_is_older(self):
        from storyloop.acceptance_repair import _current_retry_result

        current = blocked("suite-2", 74)
        state = {"status": "ORACLE_DEFECT", "contract_commit": "base",
                 "acceptance": current["acceptance"], "stop": {"status": "ORACLE_DEFECT"}}
        with patch("storyloop.acceptance_repair.load_change", return_value={"change_id": "C"}), \
             patch("storyloop.acceptance_repair.suite_digest", return_value="suite-2"):
            result = _current_retry_result(Path("/tmp"), "C", None, state, blocked("suite-1", 68))
        self.assertEqual(result["acceptance"]["suite_digest"], "suite-2")
        self.assertEqual(result["acceptance"]["entry_use_findings"][0]["line"], 74)

    def test_legacy_stop_without_digest_does_not_reprove_unchanged_suite(self):
        from storyloop.acceptance_repair import _current_retry_result

        result = blocked("old", 68)
        result["acceptance"].pop("suite_digest")
        state = {"status": "ORACLE_DEFECT", "contract_commit": "base",
                 "acceptance": result["acceptance"], "repairs": []}
        with patch("storyloop.acceptance_repair.load_change", return_value={"change_id": "C"}), \
             patch("storyloop.acceptance_repair.suite_digest", return_value="current"), \
             patch("storyloop.acceptance_repair.reprove_current_suite") as reprove:
            current = _current_retry_result(Path("/tmp"), "C", None, state, result)
        self.assertIs(current, result)
        reprove.assert_not_called()

    def test_run_reproves_new_suite_instead_of_replaying_old_stop(self):
        from storyloop.project_preparation import _prepare_acceptance

        change = {"change_id": "C"}
        state = {"change_id": "C", "status": "ORACLE_DEFECT", "acceptance": {
            **blocked("suite-1", 68)["acceptance"]},
            "repairs": [{"commit": "new-suite"}]}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            marker = root / "protected.json"
            marker.write_text("{}")
            with patch("storyloop.project_preparation.materialize_approved_change", return_value=root), \
                 patch("storyloop.project_preparation.validate_with_cli", return_value={}), \
                 patch("storyloop.project_preparation.protection_path", return_value=marker), \
                 patch("storyloop.project_preparation.load_smoke_manifest", return_value={}), \
                 patch("storyloop.project_preparation.suite_digest", return_value="suite-2"), \
                 patch("storyloop.project_preparation.load_json", return_value={
                     "change_id": "C", "status": "IMPLEMENTING", "red_evidence_sha256": "red-current"}), \
                 patch("storyloop.acceptance_repair.reprove_current_suite", return_value={
                     "status": "ACCEPTANCE_REPAIRED"}) as reprove:
                result = _prepare_acceptance(root, change, SimpleNamespace(), state)
        self.assertIsNone(result)
        reprove.assert_called_once()
        self.assertEqual(state["status"], "IMPLEMENTING")

    def test_failed_reproof_stop_names_current_suite_digest(self):
        from storyloop.acceptance_repair import _unproven_suite
        from storyloop.models import StoryLoopError

        state = {"change_id": "C", "status": "ORACLE_DEFECT", "attempt": 0}
        error = StoryLoopError("new defect", code="ORACLE_DEFECT", details={
            "fixture_repairable": True, "defect_category": "UNVERIFIED_ENTRY_USE"})
        with patch("storyloop.acceptance_repair.load_change", return_value={"change_id": "C"}), \
             patch("storyloop.acceptance_repair.suite_digest", return_value="suite-2"), \
             patch("storyloop.acceptance_repair.pause", side_effect=lambda p, s, status, **details:
                   {"status": status, **details}) as pause:
            result = _unproven_suite(Path("/tmp"), state, error)
        self.assertEqual(result["acceptance"]["suite_digest"], "suite-2")
        self.assertEqual(pause.call_args.kwargs["acceptance"]["suite_digest"], "suite-2")
        from storyloop.stop_report import _evidence
        self.assertIn("验收套件 digest: suite-2", _evidence(state, result))

    def test_reproof_exception_does_not_restore_previous_suite_stop(self):
        from storyloop.acceptance_repair import repair_smoke
        from storyloop.approvals import approve_change
        from storyloop.baseline import bootstrap_project
        from storyloop.changes import load_change
        from storyloop.models import StoryLoopError
        from storyloop.project_loop import load_run_state, run_change
        from storyloop.smoke import suite_digest
        from tests.routing_support import legacy_create_change
        from tests.support import product
        from tests.test_acceptance_fixture_retry import fixture_error
        from tests.test_baseline_behavior import repository
        from tests.test_project_loop import smoke_payload
        from tests.test_smoke_repair import services

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            bootstrap_project(root, {"test_commands": [], "acceptance_fixture_retry_limit": 0})
            change_id = legacy_create_change(root, "orders", product())["change_id"]
            approve_change(root, change_id)
            initial = run_change(root, change_id, services(change_id,
                lambda _project, _request: fixture_error(change_id, "old fixture")))
            self.assertEqual(initial["status"], "ORACLE_DEFECT")
            with patch("storyloop.acceptance_repair._prove_repaired_suite",
                       side_effect=StoryLoopError("new reproof failed", code="BLOCKED")):
                with self.assertRaises(StoryLoopError):
                    repair_smoke(root, change_id, "replace fixture", services(change_id,
                        lambda _project, _request: smoke_payload(change_id)))
            state = load_run_state(root, change_id)
            self.assertNotIn("acceptance", state)
            self.assertNotIn("stop", state)
            self.assertEqual(state["retry_recheck"]["suite_digest"],
                             suite_digest(root, load_change(root, change_id)))
            resumed = run_change(root, change_id, services(change_id,
                lambda _project, _request: smoke_payload(change_id)))
            self.assertEqual(resumed["status"], "PASSED", resumed)


if __name__ == "__main__":
    unittest.main()
