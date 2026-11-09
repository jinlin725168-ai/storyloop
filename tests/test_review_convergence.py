"""Review levels and repeated oracle-strength findings follow a bounded protocol."""

from copy import deepcopy
from pathlib import Path
import unittest
from unittest.mock import patch

from storyloop.models import StoryLoopError
from storyloop.project_contracts import validate_review
from storyloop.role_schemas import schema_errors, schema_for


def finding(level="MUST_FIX", *, finding_id="ORACLE-AC-4-01", evidence="weak assertion"):
    return {"id": finding_id, "criterion_id": "AC-4", "target": "oracle",
            "disposition": level, "blocking": level != "ADVISORY",
            "location": "acceptance/changes/FIX-demo/tests/test_ac4.py:12",
            "evidence": evidence, "message": "AC-4 tests only nonempty output",
            "required_change": "Assert the public status and failure payload"}


def review(*findings, verdict="fail"):
    return {"verdict": verdict, "summary": "AC-4 strength", "findings": list(findings),
            "questions": []}


class ReviewLevelTests(unittest.TestCase):
    def test_consistency_decision_needs_input_and_advisory_is_coherent(self):
        from storyloop.consistency import validate_consistency

        change = {"product": {"stories": [{"acceptance": [{"id": "AC-4"}]}]}}
        decision = {"verdict": "needs_input", "summary": "choose behavior", "questions": [],
                    "findings": [finding("DECISION_NEEDED")]}
        with patch("storyloop.consistency._validate_location"):
            checked = validate_consistency(decision, Path("/tmp"), change, {})
            self.assertEqual(checked["questions"], [finding()["required_change"]])
            advisory = {"verdict": "coherent", "summary": "optional", "questions": [],
                        "findings": [finding("ADVISORY")]}
            self.assertEqual(validate_consistency(advisory, Path("/tmp"), change, {})["verdict"],
                             "coherent")

    def test_decision_needed_is_not_dropped_by_model_verification(self):
        from storyloop.review_disposition import _verify

        result = {"fix": [], "stop": [finding("DECISION_NEEDED")], "focus": []}
        with patch("storyloop.review_disposition._call") as call:
            _verify(Path("/tmp"), {"change_id": "FIX-demo"}, object(), {},
                    {"tier_requested": "standard"}, result)
        call.assert_not_called()
        self.assertEqual(result["stop"][0]["disposition"], "DECISION_NEEDED")

    def test_unprofiled_review_uses_disposition_for_global_verdict(self):
        from storyloop.review_disposition import perform_review

        with patch("storyloop.review_disposition._call", return_value=review(finding("ADVISORY"))):
            result = perform_review(Path("/tmp"), {"change_id": "FIX-demo"}, object(), {}, {})
        self.assertEqual(result["verdict"], "pass")
        self.assertEqual(len(result["findings"]), 1)
        with patch("storyloop.review_disposition._call", return_value=review(finding("DECISION_NEEDED"))):
            result = perform_review(Path("/tmp"), {"change_id": "FIX-demo"}, object(), {}, {})
        self.assertEqual(result["verdict"], "needs_input")
        self.assertTrue(result["questions"])

    def test_final_review_reuses_stable_id_for_same_root(self):
        from storyloop.review_disposition import perform_review

        state = {"change_id": "FIX-demo"}
        with patch("storyloop.review_disposition._call", return_value=review(finding())):
            perform_review(Path("/tmp"), state, object(), {}, {})
        with patch("storyloop.review_disposition._call",
                   return_value=review(finding(finding_id="ORACLE-AC-4-02"))):
            with self.assertRaises(StoryLoopError):
                perform_review(Path("/tmp"), state, object(), {}, {})

    def test_profiled_classification_does_not_escalate_advisory_by_confidence(self):
        from storyloop.finding_disposition import dispose

        advisory = {**finding("ADVISORY"), "confidence": 100, "severity": "P1"}
        must_fix = {**finding("MUST_FIX", finding_id="ORACLE-AC-4-02"),
                    "confidence": 95, "severity": "P1"}
        result = dispose([advisory, must_fix], {}, [])
        self.assertEqual(result["fix"], [])
        self.assertEqual([item["agent_id"] for item in result["stop"]], ["ORACLE-AC-4-02"])
        self.assertEqual([item["agent_id"] for item in result["focus"]], ["ORACLE-AC-4-01"])

    def test_disposition_requires_matching_blocking_in_both_review_schemas(self):
        for level in ("MUST_FIX", "DECISION_NEEDED", "ADVISORY"):
            bad = finding(level)
            bad["blocking"] = not bad["blocking"]
            with self.subTest(level=level), self.assertRaises(StoryLoopError):
                validate_review(review(bad))
            consistency = {"verdict": "blocked", "summary": "bad", "questions": [],
                           "findings": [bad]}
            self.assertTrue(schema_errors(consistency, schema_for("review", {"mode": "consistency"})))

    def test_consistency_validator_rejects_level_blocking_mismatch(self):
        from storyloop.consistency import validate_consistency

        bad = finding("ADVISORY")
        bad["blocking"] = True
        change = {"product": {"stories": [{"acceptance": [{"id": "AC-4"}]}]}}
        with patch("storyloop.consistency._validate_location"), self.assertRaises(StoryLoopError):
            validate_consistency({"verdict": "blocked", "summary": "bad", "questions": [],
                                  "findings": [bad]}, Path("/tmp"), change, {})

    def test_advisory_does_not_fail_final_review_and_legacy_stays_valid(self):
        self.assertEqual(validate_review(review(finding("ADVISORY"))), "pass")
        old = {"message": "old finding", "target": "oracle", "criterion_id": "AC-4",
               "location": "acceptance/changes/FIX-demo/tests/test_ac4.py:12"}
        self.assertEqual(validate_review(review(old)), "fail")
        nullable_old = {**old, "disposition": None, "id": None, "blocking": None}
        self.assertEqual(validate_review(review(nullable_old)), "fail")
        self.assertEqual(validate_review(review(finding("ADVISORY"), old)), "fail")

    def test_decision_needed_requires_human_question(self):
        result = review(finding("DECISION_NEEDED"))
        self.assertEqual(validate_review(result), "needs_input")

    def test_delivery_gate_accepts_only_nonblocking_advisory_findings(self):
        from storyloop.run_guards import review_passes

        self.assertTrue(review_passes(review(finding("ADVISORY"), verdict="pass")))
        self.assertFalse(review_passes(review(finding("MUST_FIX"), verdict="pass")))
        self.assertFalse(review_passes(review({"message": "legacy"}, verdict="pass")))
        self.assertFalse(review_passes(review({"disposition": "ADVISORY", "blocking": False},
                                              verdict="pass")))


class ConvergenceTests(unittest.TestCase):
    def test_legacy_review_does_not_add_convergence_state(self):
        from storyloop.review_convergence import advance

        state = {}
        old = {"criterion_id": "AC-4", "target": "oracle", "blocking": True,
               "location": "acceptance/changes/FIX-demo/tests/test_ac4.py:12",
               "message": "old format"}
        self.assertIsNone(advance(state, review(old),
                                  {"contract": "c1", "plan": "p1", "review": "suite-1"}))
        self.assertEqual(state, {})

    def test_consistency_gate_persists_round_and_exhausted_stop_metadata(self):
        from storyloop.consistency import ensure_consistency
        from tests.support import product

        state = {"change_id": "FIX-demo", "attempt": 0,
                 "routing": {"effective_tier": "heavy"},
                 "planning": {"plan": {"id": "plan", "main_option": "main",
                                       "options": [{"id": "main", "dependencies": []}]}}}
        change = {"change_id": "FIX-demo", "product": product()}
        row = finding()
        row["criterion_id"] = "AC-1"
        response = {"verdict": "blocked", "summary": "weak", "questions": [], "findings": [row]}
        with patch("storyloop.consistency.is_routed", return_value=True), \
             patch("storyloop.consistency.current_binding",
                   side_effect=[{"suite_digest": "s1"}, {"suite_digest": "s2"},
                                {"suite_digest": "s3"}]), \
             patch("storyloop.consistency._valid_design", return_value=True), \
             patch("storyloop.dependency_checks.verify_dependencies", return_value=[]), \
             patch("storyloop.consistency._write_artifact"), \
             patch("storyloop.consistency.load_answers", return_value=[]), \
             patch("storyloop.consistency._review", return_value=response), \
             patch("storyloop.consistency._outcome", side_effect=lambda p, s, r: r):
            first = ensure_consistency(Path("/tmp"), change, state, object())
            second = ensure_consistency(Path("/tmp"), change, state, object())
            third = ensure_consistency(Path("/tmp"), change, state, object())
        self.assertEqual(first["status"], "ORACLE_DEFECT")
        self.assertEqual(second["status"], "ORACLE_DEFECT")
        self.assertEqual(state["review_convergence"]["criteria"]["AC-1"]["round"], 3)
        self.assertTrue(third["convergence"]["exhausted"])

    def test_next_consistency_request_contains_full_spec_fields(self):
        from storyloop.consistency import _review
        from storyloop.intake import digest
        from storyloop.review_convergence import advance

        state = {"planning": {"plan": {"id": "plan", "main_option": "main",
                                       "options": [{"id": "main"}]}, "dependency_evidence": []}}
        change = {"change_id": "FIX-demo", "product": {"stories": []}}
        binding = {"contract": digest(change["product"]),
                   "plan": digest({"main_option": "main", "options": [{"id": "main"}]}),
                   "review": "suite-1"}
        advance(state, review(finding()), binding)
        advance(state, review(finding()), {**binding, "review": "suite-2"})
        seen = []
        with patch("storyloop.consistency.load_principles", return_value={}), \
             patch("storyloop.consistency.load_answers", return_value=[]), \
             patch("storyloop.consistency.suite_relative", return_value="acceptance/changes/FIX-demo"), \
             patch("storyloop.consistency.answer_with_repair",
                   side_effect=lambda call, request, validate: seen.append(request) or
                   {"verdict": "coherent", "summary": "clear", "questions": [], "findings": []}):
            _review(Path("/tmp"), change, state, object(), "plan.json")
        self.assertEqual(seen[0]["convergence"]["AC-4"]["required_spec_fields"],
                         ["inputs", "preconditions", "entry", "expected",
                          "negative_boundary", "contract_plan"])

    def test_exhausted_stop_shows_evidence_and_recovery_commands(self):
        from storyloop.stop_report import build_stop, render_stop

        convergence = {"criterion_id": "AC-4", "round": 3,
                       "finding_ids": ["ORACLE-AC-4-01"], "missing_spec_fields": ["inputs"],
                       "prior_findings": [{"id": "ORACLE-AC-4-01", "evidence": "weak assertion",
                                           "message": "nonempty only"}], "exhausted": True}
        state = {"change_id": "FIX-demo", "status": "ORACLE_DEFECT", "attempt": 1,
                 "contract_commit": "abc", "red_evidence_sha256": "def", "failures": [],
                 "routed": {"source": "consistency", "target": "oracle", "findings": [finding()]}}
        stop = build_stop("/p", state, "ORACLE_DEFECT", {"convergence": convergence})
        shown = "\n".join(render_stop(stop))
        self.assertEqual(stop["convergence"]["criterion_id"], "AC-4")
        self.assertIn("缺少", shown)
        self.assertIn("weak assertion", shown)
        self.assertIn("acceptance-repair", str(stop["options"]))
        self.assertIn("overrule", str(stop["options"]))
        self.assertIn("supersede", str(stop["options"]))
        secret = deepcopy(convergence)
        secret["prior_findings"][0]["evidence"] = "api_key=private123"
        safe = build_stop("/p", state, "ORACLE_DEFECT", {"convergence": secret})
        self.assertNotIn("private123", str(safe))

    def test_pause_next_uses_convergence_repair_reason(self):
        from storyloop.run_stops import pause

        state = {"change_id": "FIX-demo", "status": "ORACLE_DEFECT", "attempt": 1,
                 "contract_commit": "abc", "red_evidence_sha256": "def", "failures": []}
        convergence = {"criterion_id": "AC-4", "round": 3, "finding_ids": ["F-1"],
                       "missing_spec_fields": ["inputs"], "prior_findings": [], "exhausted": True}
        with patch("storyloop.run_stops.transition"), patch("storyloop.run_stops.save_run"):
            result = pause(Path("/p"), state, "ORACLE_DEFECT", convergence=convergence)
        self.assertIn("按已批准合同补全判据", result["next"])

    def test_convergence_design_next_points_to_replan(self):
        from storyloop.stop_report import build_stop

        state = {"change_id": "FIX-demo", "status": "DESIGN_REVISION_REQUIRED", "attempt": 1,
                 "contract_commit": "abc", "red_evidence_sha256": "def", "failures": []}
        convergence = {"criterion_id": "AC-4", "round": 3, "finding_ids": ["F-1"],
                       "missing_spec_fields": [], "prior_findings": [], "exhausted": True}
        stop = build_stop("/p", state, "DESIGN_REVISION_REQUIRED", {"convergence": convergence})
        self.assertIn("--replan", stop["resume"])

    def test_two_consecutive_oracle_findings_request_full_spec_then_exhaust(self):
        from storyloop.review_convergence import advance, review_request

        state = {}
        first = review(finding())
        first_binding = {"contract": "c1", "plan": "p1", "review": "suite-1"}
        self.assertIsNone(advance(state, first, first_binding))
        self.assertIsNone(advance(state, first, first_binding))  # replay cannot count twice
        self.assertEqual(state["review_convergence"]["criteria"]["AC-4"]["round"], 1)
        second = review(finding(evidence="still accepts any nonempty value"))
        self.assertIsNone(advance(state, second, {**first_binding, "review": "suite-2"}))
        self.assertEqual(state["review_convergence"]["criteria"]["AC-4"]["round"], 2)
        request = review_request(state)
        self.assertEqual(request["AC-4"]["round"], 2)
        self.assertEqual(request["AC-4"]["finding_ids"], ["ORACLE-AC-4-01"])
        self.assertEqual([row["evidence"] for row in request["AC-4"]["prior_findings"]],
                         ["weak assertion", "still accepts any nonempty value"])
        exhausted = advance(state, review(finding()), {**first_binding, "review": "suite-3"})
        self.assertEqual(exhausted["criterion_id"], "AC-4")
        self.assertTrue(exhausted["exhausted"])
        self.assertIn("inputs", exhausted["missing_spec_fields"])
        self.assertEqual(advance(state, review(finding()), {**first_binding, "review": "suite-3"}),
                         exhausted)

    def test_complete_spec_allows_one_repair_and_one_whole_review(self):
        from storyloop.review_convergence import advance, repair_allowed, record_repair

        state = {}
        binding = {"contract": "c1", "plan": "p1", "review": "suite-1"}
        for index in (1, 2):
            advance(state, review(finding()), {**binding, "review": f"suite-{index}"})
        complete = finding()
        complete["criterion_spec"] = {"inputs": "invalid readiness config",
            "preconditions": "candidate CLI starts", "entry": "VE-CLI via storyloop run",
            "expected": "status NEEDS_INPUT", "negative_boundary": "missing path is reported",
            "contract_plan": "AC-4 and plan main entry_paths"}
        self.assertIsNone(advance(state, review(complete), {**binding, "review": "suite-3"}))
        self.assertEqual(state["review_convergence"]["criteria"]["AC-4"]["phase"], "repair_once")
        self.assertTrue(repair_allowed(state))
        record_repair(state)
        self.assertFalse(repair_allowed(state))
        exhausted = advance(state, review(finding()), {**binding, "review": "suite-4"})
        self.assertTrue(exhausted["exhausted"])
        self.assertEqual(exhausted["missing_spec_fields"], [])

    def test_full_spec_entry_must_name_approved_verification_entry(self):
        from storyloop.review_convergence import advance

        state = {}
        binding = {"contract": "c1", "plan": "p1", "review": "suite-1"}
        for index in (1, 2):
            advance(state, review(finding()), {**binding, "review": f"suite-{index}"})
        bad = finding()
        bad["criterion_spec"] = {"inputs": "invalid readiness config",
            "preconditions": "candidate CLI starts", "entry": "VE-NOPE via CLI",
            "expected": "status NEEDS_INPUT", "negative_boundary": "missing path reported",
            "contract_plan": "AC-4 and main plan"}
        exhausted = advance(state, review(bad), {**binding, "review": "suite-3"},
                            approved_entries={"AC-4": {"VE-CLI"}})
        self.assertEqual(exhausted["missing_spec_fields"], ["entry"])

    def test_full_spec_entry_must_belong_to_criterion_story(self):
        from storyloop.review_convergence import advance

        state = {}
        binding = {"contract": "c1", "plan": "p1", "review": "suite-1"}
        for index in (1, 2):
            advance(state, review(finding()), {**binding, "review": f"suite-{index}"})
        wrong_story = finding()
        wrong_story["criterion_spec"] = {"inputs": "invalid readiness config",
            "preconditions": "candidate CLI starts", "entry": "VE-OTHER via CLI",
            "expected": "status NEEDS_INPUT", "negative_boundary": "missing path reported",
            "contract_plan": "AC-4 and main plan"}
        exhausted = advance(state, review(wrong_story), {**binding, "review": "suite-3"},
                            approved_entries={"AC-4": {"VE-CLI"}, "AC-5": {"VE-OTHER"}})
        self.assertEqual(exhausted["missing_spec_fields"], ["entry"])
        accepted_state = {}
        for index in (1, 2):
            advance(accepted_state, review(finding()), {**binding, "review": f"suite-{index}"})
        correct = deepcopy(wrong_story)
        correct["criterion_spec"]["entry"] = "VE-CLI via CLI"
        self.assertIsNone(advance(accepted_state, review(correct),
                                  {**binding, "review": "suite-3"},
                                  approved_entries={"AC-4": {"VE-CLI"}}))

    def test_contract_reclassification_keeps_convergence_metadata(self):
        from storyloop.review_convergence import advance

        state = {}
        binding = {"contract": "c1", "plan": "p1", "review": "suite-1"}
        advance(state, review(finding()), binding)
        advance(state, review(finding()), {**binding, "review": "suite-2"})
        contract = finding(finding_id="CONTRACT-AC-4-01")
        contract["target"] = "contract"
        contract["location"] = "openspec/changes/FIX-demo/stories.json:10"
        result = advance(state, review(contract), {**binding, "review": "suite-3"})
        self.assertEqual(result["criterion_id"], "AC-4")
        self.assertTrue(result["exhausted"])

    def test_intervening_clear_round_resets_and_binding_change_records_reason(self):
        from storyloop.review_convergence import advance, review_request

        state = {}
        binding = {"contract": "c1", "plan": "p1", "review": "suite-1"}
        advance(state, review(finding()), binding)
        advance(state, review(verdict="coherent"), {**binding, "review": "suite-2"})
        advance(state, review(finding()), {**binding, "review": "suite-3"})
        self.assertEqual(state["review_convergence"]["criteria"]["AC-4"]["round"], 1)
        advance(state, review(finding()), {"contract": "c1", "plan": "p2", "review": "suite-4"})
        self.assertEqual(state["review_convergence"]["criteria"]["AC-4"]["round"], 1)
        self.assertEqual(state["review_convergence"]["resets"][-1]["reason"], "plan_binding_changed")
        advance(state, review(finding()), {"contract": "c1", "plan": "p2", "review": "suite-5"})
        self.assertEqual(review_request(state, {"contract": "c1", "plan": "p3"}), {})

    def test_explicit_binding_invalidation_resets_and_records_reason(self):
        from storyloop.review_convergence import advance, reset_binding

        state = {}
        advance(state, review(finding()), {"contract": "c1", "plan": "p1", "review": "suite-1"})
        reset_binding(state, "plan_binding_invalid")
        self.assertEqual(state["review_convergence"]["criteria"], {})
        self.assertEqual(state["review_convergence"]["resets"][-1]["reason"],
                         "plan_binding_invalid")

    def test_same_root_keeps_id_and_new_root_cannot_reuse_it(self):
        from storyloop.review_convergence import advance

        state = {}
        binding = {"contract": "c1", "plan": "p1", "review": "suite-1"}
        advance(state, review(finding()), binding)
        moved_line = finding()
        moved_line["location"] = "acceptance/changes/FIX-demo/tests/test_ac4.py:19"
        advance(state, review(moved_line), {**binding, "review": "suite-2"})
        changed_id = finding(finding_id="ORACLE-AC-4-02")
        with self.assertRaises(StoryLoopError):
            advance(state, review(changed_id), {**binding, "review": "suite-3"})
        other_root = deepcopy(finding())
        other_root["location"] = "acceptance/changes/FIX-demo/tests/test_other.py:9"
        with self.assertRaises(StoryLoopError):
            advance(state, review(other_root), {**binding, "review": "suite-3"})

    def test_multiple_findings_on_one_ac_count_one_round_and_keep_ids(self):
        from storyloop.review_convergence import advance

        other = finding(finding_id="ORACLE-AC-4-02")
        other["location"] = "acceptance/changes/FIX-demo/tests/test_edge.py:8"
        state = {}
        advance(state, review(finding(), other),
                {"contract": "c1", "plan": "p1", "review": "suite-1"})
        row = state["review_convergence"]["criteria"]["AC-4"]
        self.assertEqual(row["round"], 1)
        self.assertEqual(row["finding_ids"], ["ORACLE-AC-4-01", "ORACLE-AC-4-02"])

    def test_distinct_failure_mechanism_in_same_file_gets_new_id(self):
        from storyloop.review_convergence import advance

        other = finding(finding_id="ORACLE-AC-4-02")
        other["required_change"] = "Reject a missing readiness field"
        state = {}
        advance(state, review(finding(), other),
                {"contract": "c1", "plan": "p1", "review": "suite-1"})
        self.assertEqual(state["review_convergence"]["criteria"]["AC-4"]["round"], 1)


if __name__ == "__main__":
    unittest.main()
