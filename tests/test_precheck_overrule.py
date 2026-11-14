import unittest
from pathlib import Path
from unittest.mock import patch

from storyloop.models import StoryLoopError
from storyloop.oracle_strength_precheck import assess
from storyloop.project_loop_failures import apply_overrule, require_routed_choice
from tests.test_oracle_strength_precheck import CHANGE, finding


class PrecheckOverruleTests(unittest.TestCase):
    def test_resume_after_precheck_stop_proves_red_and_continues(self):
        import tempfile
        from storyloop.approvals import approve_change
        from storyloop.baseline import bootstrap_project
        from storyloop.project_loop import load_run_state, run_change
        from storyloop.run_services import LoopServices
        from storyloop.smoke import RED_EVIDENCE
        from storyloop.storage import load_json
        from tests.routing_support import legacy_create_change
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
            change_id = legacy_create_change(root, "orders", contract)["change_id"]
            approve_change(root, change_id)
            calls = []

            def generate(project, request):
                answer = smoke_payload(change_id)
                answer["manifest"]["tests"][0]["entry_ids"] = ["VE-1"]
                return answer

            def review(project, context):
                calls.append(context.get("mode"))
                if context.get("mode") == "oracle-strength":
                    return {"verdict": "fail", "summary": "contract dispute",
                            "findings": [finding("MUST_FIX")], "questions": []}
                return {"verdict": "pass", "summary": "ok", "findings": [], "questions": []}

            def implement(project, context):
                path = project / "modules/orders/service.py"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("VALUE = 1\n", encoding="utf-8")
                return {"summary": "done", "questions": []}

            service = LoopServices(generate_smoke=generate, implement=implement, review=review)
            def no_progress(project, cid, services, stopped):
                from storyloop.project_loop_support import save_run
                state = load_run_state(project, cid)
                state["acceptance"]["retry_stop"] = {"reason": "no_progress"}
                save_run(project, state)
                return stopped

            with patch("storyloop.project_preparation.check_entry_use"), \
                 patch("storyloop.acceptance_repair.retry_fixture_red", side_effect=no_progress):
                first = run_change(root, change_id, service)
            self.assertEqual(first["status"], "ORACLE_DEFECT")
            self.assertEqual(load_run_state(root, change_id)["acceptance"]["retry_stop"]["reason"],
                             "no_progress")
            with patch("storyloop.project_preparation.check_entry_use"):
                result = run_change(root, change_id, service, overrule="超出合同")
            self.assertEqual(result["status"], "PASSED", result)
            self.assertEqual(calls.count("oracle-strength"), 1)
            red = load_json(root / ".storyloop/evidence" / change_id / RED_EVIDENCE)
            self.assertEqual(red["oracle_strength_precheck"]["human_overrule"]["reason"], "超出合同")
            self.assertEqual(load_run_state(root, change_id)["overrules"][0]["source"], "precheck")

    def state(self):
        precheck = {"verdict": "fail", "suite_digests": {"manifest.json": "abc"},
                    "findings": [finding("MUST_FIX")]}
        return {"change_id": "C", "status": "ORACLE_DEFECT", "attempt": 1,
                "acceptance": {"status": "ORACLE_DEFECT", "fixture_repairable": True,
                               "defect_category": "ORACLE_STRENGTH_PRECHECK",
                               "precheck": precheck,
                               "retry_stop": {"reason": "no_progress"}}}

    def test_no_progress_can_be_overruled_and_red_uses_pass(self):
        state = self.state()
        require_routed_choice(state, "合同未要求该否定改写")
        with patch("storyloop.smoke.smoke_digests", return_value={"manifest.json": "abc"}), \
             patch("storyloop.changes.load_change", return_value=CHANGE), \
             patch("storyloop.project_loop_failures.save_run"):
            self.assertIsNone(apply_overrule(Path("/tmp"), state, "合同未要求该否定改写"))
            result = assess(Path("/tmp"), CHANGE, state, None, {"tests": []}, {})
        self.assertEqual(result["verdict"], "pass")
        self.assertEqual(result["human_overrule"]["finding_ids"], ["ORACLE-A01"])
        self.assertEqual(state["overrules"][0]["reason"], "合同未要求该否定改写")
        self.assertEqual(state["intervention_events"][0]["source"], "precheck")

    def test_deterministic_defect_still_rejected(self):
        state = self.state()
        state["acceptance"]["defect_category"] = "UNVERIFIED_ENTRY_USE"
        with self.assertRaises(StoryLoopError):
            require_routed_choice(state, "ignore")

    def test_stop_offers_overrule_only_for_precheck(self):
        from storyloop.stop_report import _stop_actions
        state = self.state()
        options, _ = _stop_actions(Path("/tmp"), state, "ORACLE_DEFECT", {})
        self.assertTrue(any("--overrule" in row["command"] for row in options))
        state["acceptance"]["defect_category"] = "UNVERIFIED_ENTRY_USE"
        options, _ = _stop_actions(Path("/tmp"), state, "ORACLE_DEFECT", {})
        self.assertFalse(any("--overrule" in row["command"] for row in options))

    def test_changed_suite_invalidates_human_decision(self):
        state = self.state()
        with patch("storyloop.smoke.smoke_digests", return_value={"manifest.json": "abc"}), \
             patch("storyloop.changes.load_change", return_value=CHANGE), \
             patch("storyloop.project_loop_failures.save_run"):
            apply_overrule(Path("/tmp"), state, "合同未要求")
        with patch("storyloop.smoke.smoke_digests", return_value={"manifest.json": "changed"}), \
             patch("storyloop.oracle_strength_precheck.read_only_call", side_effect=lambda p, c, fn: fn()):
            from types import SimpleNamespace
            service = SimpleNamespace(review=lambda p, c: {"verdict": "fail", "summary": "again",
                                      "findings": [finding("MUST_FIX")], "questions": []})
            with self.assertRaises(StoryLoopError) as caught:
                assess(Path("/tmp"), CHANGE, state, service, {"tests": []}, {})
        self.assertEqual(caught.exception.code, "ORACLE_DEFECT")

    def test_changed_suite_cannot_use_stale_stopped_review(self):
        state = self.state()
        with patch("storyloop.smoke.smoke_digests", return_value={"manifest.json": "changed"}), \
             patch("storyloop.changes.load_change", return_value=CHANGE):
            with self.assertRaisesRegex(StoryLoopError, "套件已变化"):
                apply_overrule(Path("/tmp"), state, "合同未要求")
        self.assertNotIn("overrules", state)

    def test_report_and_decisions_show_human_ruling(self):
        from storyloop.openspec_docs import render_decisions
        from storyloop.report_markdown import _render_item
        state = self.state()
        with patch("storyloop.smoke.smoke_digests", return_value={"manifest.json": "abc"}), \
             patch("storyloop.changes.load_change", return_value=CHANGE), \
             patch("storyloop.project_loop_failures.save_run"):
            apply_overrule(Path("/tmp"), state, "超出合同")
        decision = state["overrules"][0]
        self.assertIn("人工推翻判据强度预审", render_decisions(
            {"change_id": "C"}, [], [], [], overrules=[decision]))
        rendered = "\n".join(_render_item({"id": "oracle_strength_precheck", "source": "human",
                                         "availability": "AVAILABLE",
                                         "data": state["oracle_strength_precheck"]}, {}))
        self.assertIn("人工推翻", rendered)
        self.assertIn("超出合同", rendered)
