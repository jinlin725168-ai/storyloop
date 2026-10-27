"""Stopping is represented by one evidence-backed, shell-safe report (2a section 6)."""

from pathlib import Path
import shlex
import unittest
from unittest.mock import patch

from storyloop.cli import parser
from storyloop.run_stops import pause, pause_for_boundary
from storyloop.run_recovery import reset_run
from storyloop.stop_report import STOP_STATUSES, build_stop, failure_view, render_stop, stop_of


KEYS = {
    "status", "phase", "completed", "last_verdict", "attempts", "blocker",
    "evidence", "facts", "questions", "options", "resume", "routing",
}


def state_for(status: str, **extra) -> dict:
    return {
        "change_id": "FEAT-1", "status": status, "attempt": 1,
        "contract_commit": "abc", "red_evidence_sha256": "def", "failures": [],
        **extra,
    }


class StopReportTests(unittest.TestCase):
    def test_e1_human_base_review_keeps_named_human_command(self):
        stop = build_stop("/project", state_for("BASE_REVIEW_REQUIRED", implementer="human"),
                          "BASE_REVIEW_REQUIRED", {})
        self.assertIn("base-review", stop["options"][0]["command"])
        self.assertIn("--verdict pass", stop["options"][0]["command"])
        self.assertNotIn("--human-review", stop["options"][0]["command"])

    def test_base_design_summary_is_bounded_and_single_line(self):
        plan = {"plan_id": "p", "goal": "目标\n" + "长" * 500,
                "paths": ["a.txt"] * 20, "steps": ["步骤\n说明"] * 20,
                "risks": ["风险"] * 20, "rollback": "回退\n说明"}
        stop = build_stop("/project", state_for("DESIGN_APPROVAL_REQUIRED",
                                                base_design=plan),
                          "DESIGN_APPROVAL_REQUIRED", {})
        summary = stop["plan_summary"]
        self.assertLessEqual(len(summary["goal"]), 240)
        self.assertFalse(any("\n" in value for value in
                             [summary["goal"], summary["rollback"], *summary["steps"]]))
        self.assertLessEqual(len(summary["paths"]), 8)
        self.assertLessEqual(len(summary["steps"]), 5)

    def test_post_l3_ready_does_not_override_current_execution_phase(self):
        state = state_for('MANUAL_ACCEPTANCE_REQUIRED',
                          planning={'origin': 'L3', 'phase': 'READY'}, phase='planning')
        stop = build_stop('/p', state, 'MANUAL_ACCEPTANCE_REQUIRED', {'phase': 'verify'})
        self.assertEqual(stop['phase'], 'verify')
        state.pop('phase')  # prepare_execution clears this once L3 reaches READY.
        self.assertEqual(build_stop('/p', state, 'MANUAL_ACCEPTANCE_REQUIRED', {})['phase'], 'verify')
        state['phase'] = 'implement'
        self.assertEqual(build_stop('/p', state, 'BOUNDARY_VIOLATION', {})['phase'], 'implement')

    def test_every_stop_has_the_same_shape_and_parseable_commands(self):
        cli = parser()
        for status in STOP_STATUSES:
            stop = build_stop("/tmp/project with space", state_for(status), status, {})
            self.assertEqual(set(stop), KEYS, status)
            self.assertTrue(stop["blocker"], status)
            if "--supersede" not in stop["resume"] and "--overrule" not in stop["resume"]:
                cli.parse_args(shlex.split(stop["resume"])[1:])
            for option in stop["options"]:
                if "--supersede" not in option["command"] and "--overrule" not in option["command"]:
                    cli.parse_args(shlex.split(option["command"])[1:])

    def test_review_routed_stops_offer_overrule_and_2a_stops_offer_supersede(self):
        routed = state_for("ORACLE_DEFECT", routed={"source": "review", "target": "oracle"})
        oracle_commands = [item["command"] for item in build_stop("/p", routed, "ORACLE_DEFECT", {})["options"]]
        self.assertTrue(any("--overrule" in command for command in oracle_commands))
        for status in ("BUDGET_EXHAUSTED", "NO_PROGRESS", "NEEDS_SPEC_FIX"):
            commands = [item["command"] for item in build_stop("/p", state_for(status), status, {})["options"]]
            self.assertTrue(any("--supersede" in command for command in commands), status)

    def test_plain_needs_spec_fix_does_not_claim_run_can_resume_it(self):
        stop = build_stop("/p", state_for("NEEDS_SPEC_FIX"), "NEEDS_SPEC_FIX", {})
        self.assertIn("--supersede", stop["resume"])
        self.assertNotEqual(stop["resume"], "storyloop run /p --change FEAT-1")

    def test_boundary_violation_offers_restore_and_run_before_expanding_the_base(self):
        stop = build_stop("/p", state_for("BOUNDARY_VIOLATION"), "BOUNDARY_VIOLATION", {})
        self.assertEqual(stop["options"][0]["action"], "还原越界文件后继续")
        self.assertIn("storyloop run", stop["options"][0]["command"])
        self.assertTrue(any("base-propose" in item["command"] for item in stop["options"]))

    def test_review_routing_drives_phase_verdict_and_location_evidence(self):
        state = state_for("ORACLE_DEFECT", routed={
            "source": "review", "target": "oracle", "findings": [{
                "message": "候选副本读取不到 .git",
                "location": "acceptance/changes/FEAT-1/t.py:70",
            }],
        })
        stop = build_stop("/p", state, "ORACLE_DEFECT", {})
        self.assertEqual(stop["phase"], "verify")
        self.assertEqual(stop["last_verdict"], {
            "gate": "review", "summary": "候选副本读取不到 .git",
        })
        self.assertIn("acceptance/changes/FEAT-1/t.py:70", stop["evidence"])

    def test_prepare_oracle_defect_does_not_claim_a_review_verdict(self):
        stop = build_stop("/p", state_for("ORACLE_DEFECT"), "ORACLE_DEFECT", {
            "acceptance": {"criteria_failing": ["AC-1"]},
        })
        self.assertEqual(stop["phase"], "prepare")
        self.assertIsNone(stop["last_verdict"])

    def test_needs_input_phase_follows_completed_prepare_evidence(self):
        contract = state_for("NEEDS_INPUT")
        contract.pop("contract_commit")
        contract.pop("red_evidence_sha256")
        self.assertEqual(build_stop("/p", contract, "NEEDS_INPUT", {})["phase"], "contract")
        preparing = state_for("NEEDS_INPUT")
        self.assertEqual(build_stop("/p", preparing, "NEEDS_INPUT", {})["phase"], "prepare")
        implementing = state_for("NEEDS_INPUT", design_plan={"status": "DONE"})
        self.assertEqual(build_stop("/p", implementing, "NEEDS_INPUT", {})["phase"], "implement")

    def test_explicit_phase_and_verdict_are_preserved(self):
        stop = build_stop("/p", state_for("WORKSPACE_TAINTED"), "WORKSPACE_TAINTED", {
            "phase": "finish", "last_verdict": {"gate": "workspace", "summary": "收尾产生额外文件"},
        })
        self.assertEqual(stop["phase"], "finish")
        self.assertEqual(stop["last_verdict"]["gate"], "workspace")

    def test_attempts_carry_attribution_and_short_signature(self):
        state = state_for("BUDGET_EXHAUSTED", failures=[{
            "attempt": 1, "attribution": "environment", "signature": "0123456789abcdef",
            "unit": {"status": "FAILED", "results": []},
        }])
        [attempt] = build_stop("/p", state, "BUDGET_EXHAUSTED", {})["attempts"]
        self.assertEqual((attempt["attribution"], attempt["signature"]), ("environment", "01234567"))

    def test_agent_unavailable_is_evidence_without_becoming_the_run_status(self):
        state = state_for("IMPLEMENTING", agent_failures=[{
            "attempt": 2, "role": "implement", "code": "AGENT_UNAVAILABLE",
            "message": "agent timed out", "call": "call-7",
        }])
        stop = build_stop("/p", state, "AGENT_UNAVAILABLE", {})
        self.assertEqual(state["status"], "IMPLEMENTING")
        self.assertEqual(stop["status"], "AGENT_UNAVAILABLE")
        self.assertTrue(any("AGENT_UNAVAILABLE" in item and "call-7" in item for item in stop["evidence"]))

    def test_free_text_and_paths_are_shell_quoted(self):
        stop = build_stop("/tmp/a project; touch BAD", state_for("NEEDS_INPUT"), "NEEDS_INPUT", {
            "questions": ["which?"],
        })
        tokens = shlex.split(stop["options"][0]["command"])
        self.assertIn("/tmp/a project; touch BAD", tokens)
        self.assertIn("<回答>", tokens)
        self.assertEqual(tokens[0], "storyloop")

    def test_legacy_stop_is_built_but_active_run_has_no_stop(self):
        self.assertEqual(stop_of("/p", state_for("BUDGET_EXHAUSTED"))["status"], "BUDGET_EXHAUSTED")
        self.assertIsNone(stop_of("/p", state_for("IMPLEMENTING")))

    def test_saved_stop_is_returned_unchanged(self):
        saved = {"status": "NO_PROGRESS", "blocker": "saved"}
        run = state_for("NO_PROGRESS", stop=saved)
        self.assertIs(stop_of("/p", run), saved)

    def test_saved_stop_for_an_old_status_is_rebuilt(self):
        stale = {"status": "NO_PROGRESS", "blocker": "stale"}
        run = state_for("BUDGET_EXHAUSTED", stop=stale)
        rebuilt = stop_of("/p", run)
        self.assertEqual(rebuilt["status"], "BUDGET_EXHAUSTED")
        self.assertIsNot(rebuilt, stale)

    def test_render_orders_blocker_evidence_questions_options_and_resume(self):
        stop = build_stop("/p", state_for("NO_PROGRESS"), "NO_PROGRESS", {
            "error": "same failure", "questions": ["继续吗？"], "violations": [{"path": "a.py"}],
        })
        text = "\n".join(render_stop(stop))
        labels = [text.index(label) for label in ("阻塞点", "证据", "问题", "可选操作", "恢复")]
        self.assertEqual(labels, sorted(labels))


class FailureViewCompatibilityTests(unittest.TestCase):
    def test_red_layer_is_not_hidden_by_a_review_without_findings(self):
        for verdict in ("pass", "fail"):
            with self.subTest(verdict=verdict):
                view = failure_view({"attempt": 1, "acceptance": {"status": "FAILED"},
                                     "review": {"verdict": verdict, "summary": "ok", "findings": []}})
                self.assertEqual(view, {"attempt": 1, "gate": "layers", "reason": "acceptance=FAILED"})

    def test_findings_and_questions_keep_review_priority_over_red_layers(self):
        for review in ({"verdict": "fail", "findings": [{"message": "bad oracle"}]},
                       {"verdict": "needs_input", "summary": "clarify", "findings": []}):
            with self.subTest(review=review):
                view = failure_view({"attempt": 1, "acceptance": {"status": "FAILED"}, "review": review})
                self.assertEqual(view["gate"], "review")

    def test_legacy_review_and_layer_failures_keep_the_old_view(self):
        self.assertEqual(failure_view({
            "attempt": 2, "review": {"findings": [{"message": "broken"}]},
        }), {"attempt": 2, "gate": "review", "reason": "1 finding(s): broken"})
        self.assertEqual(failure_view({
            "attempt": 3, "unit": {"status": "FAILED"}, "smoke": {"status": "PASSED"},
        }), {"attempt": 3, "gate": "layers", "reason": "unit=FAILED"})

    def test_boundary_failure_names_its_category_and_paths(self):
        view = failure_view({
            "attempt": 4,
            "boundary": {
                "status": "BOUNDARY_VIOLATION",
                "violations": [
                    {"category": "outside_allowed_roots", "path": "docs/a.md"},
                    {"category": "protected", "path": ".storyloop/config.json"},
                ],
            },
        })
        self.assertEqual(view["gate"], "boundary")
        self.assertIn("BOUNDARY_VIOLATION", view["reason"])
        self.assertIn("outside_allowed_roots: docs/a.md", view["reason"])


class StopPersistenceTests(unittest.TestCase):
    @patch("storyloop.run_stops.save_run")
    def test_pause_saves_and_returns_the_same_stop_report(self, save_run):
        state = state_for("IMPLEMENTING")
        result = pause(Path("/p"), state, "NO_PROGRESS", error="same failure")
        save_run.assert_called_once_with(Path("/p"), state)
        self.assertIs(result["stop"], state["stop"])
        self.assertEqual(state["stop"]["status"], "NO_PROGRESS")
        self.assertTrue(result["next"])

    @patch("storyloop.run_stops.save_run")
    def test_boundary_pause_passes_an_explicit_phase_to_the_report(self, _save_run):
        state = state_for("IMPLEMENTING")
        result = pause_for_boundary(Path("/p"), state, {
            "status": "BOUNDARY_VIOLATION", "violations": [], "base_proposal": None,
        }, phase="prepare")
        self.assertEqual(result["stop"]["phase"], "prepare")

    @patch("storyloop.run_stops.save_run")
    def test_one_stop_explicit_phase_does_not_leak_into_a_later_stop(self, _save_run):
        state = state_for("IMPLEMENTING", design_plan={"status": "DONE"})
        pause_for_boundary(Path("/p"), state, {
            "status": "BOUNDARY_VIOLATION", "violations": [], "base_proposal": None,
        }, phase="prepare")
        self.assertNotIn("phase", state)
        later = pause(Path("/p"), state, "BUDGET_EXHAUSTED")
        self.assertEqual(later["stop"]["phase"], "implement")

    @patch("storyloop.run_state.write_json")
    @patch("storyloop.run_recovery.current_branch", return_value="feature/FEAT-1")
    @patch("storyloop.run_recovery.load_json")
    @patch("storyloop.run_recovery.load_change")
    @patch("pathlib.Path.exists", return_value=True)
    def test_reset_removes_a_stale_saved_stop(self, _exists, load_change, load_json,
                                               _current_branch, write_json):
        load_change.return_value = {"product": {"nature": "feature"},
                                    "approval": {"contract_digest": "digest"}}
        state = state_for(
            "WORKSPACE_TAINTED", contract_digest="digest", feature_branch="feature/FEAT-1",
            stop={"status": "WORKSPACE_TAINTED"},
        )
        load_json.return_value = state
        with patch("storyloop.run_recovery.locked"):
            reset_run(Path("/p"), "FEAT-1", "cleaned")
        saved = write_json.call_args.args[1]
        self.assertEqual(saved["status"], "IMPLEMENTING")
        self.assertNotIn("stop", saved)


if __name__ == "__main__":
    unittest.main()
