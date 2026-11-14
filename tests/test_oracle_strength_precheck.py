import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from storyloop.models import StoryLoopError
from storyloop.oracle_strength_precheck import assess, enabled


def finding(level):
    return {"id": "ORACLE-A01", "criterion_id": "AC-1", "target": "oracle",
            "disposition": level, "blocking": level != "ADVISORY",
            "location": "acceptance/test.py:8", "evidence": "nested branch absent",
            "message": "missing nested branch", "required_change": "exercise nested path"}


CHANGE = {"change_id": "C", "product": {"stories": [{"acceptance": [],
          "verification_entries": [{"id": "VE-1", "kind": "cli", "locator": "tool run",
                                    "observable": "result", "reason": "public"}]}]}}


class OracleStrengthPrecheckTests(unittest.TestCase):
    def test_must_fix_rewrites_once_then_proves_red(self):
        import tempfile
        from storyloop.approvals import approve_change
        from storyloop.baseline import bootstrap_project
        from tests.routing_support import legacy_create_change as create_change
        from storyloop.project_loop import run_change, load_run_state
        from storyloop.run_services import LoopServices
        from tests.support import product
        from tests.test_baseline_behavior import repository
        from tests.test_project_loop import smoke_payload

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            bootstrap_project(root, {"test_commands": []})
            contract = product()
            contract["stories"][0]["verification_entries"] = CHANGE["product"]["stories"][0]["verification_entries"]
            contract["stories"][0]["acceptance"][0]["entry_ids"] = ["VE-1"]
            change_id = create_change(root, "orders", contract)["change_id"]
            approve_change(root, change_id)
            reviews = []
            suites = []

            def generate(project, request):
                suites.append(request)
                answer = smoke_payload(change_id)
                answer["manifest"]["tests"][0]["entry_ids"] = ["VE-1"]
                answer["files"][0]["content"] += f"# suite {len(suites)}\n"
                return answer

            def review(project, context):
                reviews.append(context.get("mode"))
                if context.get("mode") == "oracle-strength" and reviews.count("oracle-strength") == 1:
                    return {"verdict": "fail", "summary": "nested missing", "findings": [finding("MUST_FIX")],
                            "questions": []}
                return {"verdict": "pass", "summary": "ok", "findings": [], "questions": []}

            def implement(project, context):
                path = project / "modules/orders/service.py"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("VALUE = 1\n", encoding="utf-8")
                return {"summary": "done", "questions": []}

            service = LoopServices(generate_smoke=generate, implement=implement, review=review)
            with patch("storyloop.project_preparation.check_entry_use"), \
                 patch("storyloop.acceptance_repair.check_entry_use"):
                result = run_change(root, change_id, service)
            self.assertEqual(result["status"], "PASSED", result)
            self.assertEqual(reviews.count("oracle-strength"), 2)
            self.assertEqual(len(suites), 2)
            state = load_run_state(root, change_id)
            self.assertTrue(state["repairs"][0]["automatic"])
            self.assertEqual(state["intervention_events"][-1]["defect_category"],
                             "ORACLE_STRENGTH_PRECHECK")
            self.assertEqual(state["repairs"][0]["reason"], "RED 证明中夹具/前提失败")
            from storyloop.storage import load_json
            from storyloop.smoke import RED_EVIDENCE
            red = load_json(root / ".storyloop/evidence" / change_id / RED_EVIDENCE)
            self.assertEqual(red["oracle_strength_precheck"]["verdict"], "pass")

    def test_automatic_repair_does_not_count_as_nonroutine(self):
        from storyloop.bench_run import _events
        routine, nonroutine, unknown = _events([
            {"kind": "acceptance_repair", "automatic": True, "defect_category": "ORACLE_STRENGTH_PRECHECK"}])
        self.assertFalse(routine)
        self.assertFalse(nonroutine)
        self.assertEqual(unknown, 0)

    def test_repeated_strength_block_stops_on_no_progress(self):
        from storyloop.acceptance_repair import retry_fixture_red

        state = {"change_id": "C", "repairs": [], "acceptance": {
            "fixture_repairable": True, "defect_category": "ORACLE_STRENGTH_PRECHECK",
            "precheck": {"findings": [finding("MUST_FIX")]}}}
        stopped = {"status": "ORACLE_DEFECT", "acceptance": {
            **state["acceptance"]}}
        def repair(*args, **kwargs):
            state["repairs"].append({"automatic": True, "defect_category": "ORACLE_STRENGTH_PRECHECK"})
            return stopped

        with patch("storyloop.acceptance_repair.load_project_config", return_value={
                 "acceptance_fixture_retry_limit": 2}), \
             patch("storyloop.acceptance_repair.load_json", return_value=state), \
             patch("storyloop.acceptance_repair.save_run"), \
             patch("storyloop.acceptance_repair.pause", side_effect=lambda p, s, status, **d:
                   {"status": status, **d}), \
             patch("storyloop.acceptance_repair._repair_once", side_effect=repair) as call:
            result = retry_fixture_red(Path("/tmp"), "C", None, stopped)
        self.assertEqual(result["status"], "ORACLE_DEFECT")
        self.assertEqual(result["acceptance"]["retry_stop"]["reason"], "no_progress")
        self.assertEqual(call.call_count, 1)

    def test_must_fix_returns_repairable_oracle_defect(self):
        answer = {"verdict": "fail", "summary": "nested missing", "findings": [finding("MUST_FIX")],
                  "questions": []}
        service = SimpleNamespace(review=lambda project, context: answer)
        with patch("storyloop.smoke.smoke_digests", return_value={}), \
             patch("storyloop.oracle_strength_precheck.read_only_call", side_effect=lambda p, c, fn: fn()):
            with self.assertRaises(StoryLoopError) as caught:
                assess(Path("/tmp"), CHANGE,
                       {"routing": {"effective_tier": "standard"}}, service, {"tests": []}, {})
        self.assertEqual(caught.exception.code, "ORACLE_DEFECT")
        self.assertTrue(caught.exception.details["fixture_repairable"])
        self.assertEqual(caught.exception.details["defect_category"], "ORACLE_STRENGTH_PRECHECK")

    def test_recheck_receives_previous_blocking_ids_for_stable_reuse(self):
        requests = []
        answer = {"verdict": "pass", "summary": "ok", "findings": [], "questions": []}
        service = SimpleNamespace(review=lambda project, context:
                                  requests.append(context) or answer)
        state = {"routing": {"effective_tier": "standard"},
                 "acceptance": {"precheck": {"findings": [finding("MUST_FIX")]}}}
        with patch("storyloop.smoke.smoke_digests", return_value={}), \
             patch("storyloop.oracle_strength_precheck.read_only_call", side_effect=lambda p, c, fn: fn()):
            assess(Path("/tmp"), CHANGE, state, service, {"tests": []}, {})
        self.assertEqual(requests[0]["previous_findings"][0]["id"], "ORACLE-A01")

    def test_decision_needed_stops_without_auto_repair(self):
        answer = {"verdict": "needs_input", "summary": "disputed", "findings": [finding("DECISION_NEEDED")],
                  "questions": ["Which boundary?"]}
        service = SimpleNamespace(review=lambda project, context: answer)
        with patch("storyloop.smoke.smoke_digests", return_value={}), \
             patch("storyloop.oracle_strength_precheck.read_only_call", side_effect=lambda p, c, fn: fn()):
            with self.assertRaises(StoryLoopError) as caught:
                assess(Path("/tmp"), CHANGE,
                       {"routing": {"effective_tier": "standard"}}, service, {"tests": []}, {})
        self.assertEqual(caught.exception.code, "NEEDS_INPUT")

    def test_light_can_disable_but_standard_cannot(self):
        self.assertFalse(enabled({"routing": {"effective_tier": "light"}},
                                 {"oracle_strength_precheck": False}))
        self.assertTrue(enabled({"routing": {"effective_tier": "standard"}},
                                {"oracle_strength_precheck": False}))


if __name__ == "__main__":
    unittest.main()
