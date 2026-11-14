"""Automatic acceptance retries converge on blocking evidence, not a fixed rework count."""

import unittest
from pathlib import Path
from unittest.mock import patch

from storyloop.models import StoryLoopError


def blocked(*ids):
    findings = [{"id": item, "criterion_id": "AC-1", "target": "oracle",
                 "disposition": "MUST_FIX", "blocking": True,
                 "location": "acceptance/test.py:12", "message": f"rule {item}"}
                for item in ids]
    return {"status": "ORACLE_DEFECT", "acceptance": {
        "fixture_repairable": True, "defect_category": "ORACLE_STRENGTH_PRECHECK",
        "precheck": {"findings": findings}}}


def deterministic(category, message):
    return {"status": "ORACLE_DEFECT", "acceptance": {
        "fixture_repairable": True, "defect_category": category,
        "criteria_failing": ["AC-1"], "test_ids": ["TEST-1"],
        "diagnostics": [{"case_id": "TEST-1", "kind": "fail", "message": message}]}}


class AcceptanceProgressRetryTests(unittest.TestCase):
    def test_red_blocking_signature_uses_criterion_file_and_mechanism(self):
        from storyloop.acceptance_defects import red_defect_context

        manifest = {"tests": [{"id": "TEST-1", "criteria": ["AC-2"],
                               "source_files": ["acceptance/check.py"]}]}
        rows = [{"id": "TEST-1", "status": "ERROR", "criteria": ["AC-2"],
                 "counts": {"diagnostics": [{"case_id": "TEST-1", "kind": "error",
                                             "message": "ImportError: first cause"}]}}]
        evidence = red_defect_context(manifest, {"product": {"stories": []}}, rows,
                                      "MANIFEST_OR_SOURCE_BOUNDARY")
        self.assertEqual(evidence["blockers"], [{"criterion_id": "AC-2",
            "path": "acceptance/check.py", "mechanism": "ImportError: first cause",
            "test_id": "TEST-1"}])

    def test_unittest_traceback_mechanism_distinguishes_new_causes(self):
        from storyloop.acceptance_defects import red_defect_context

        manifest = {"tests": [{"id": "TEST-1", "criteria": ["AC-2"],
                               "source_files": ["acceptance/check.py"]}]}
        causes = []
        for message in ("ImportError: missing alpha", "ImportError: missing beta"):
            row = {"id": "TEST-1", "status": "ERROR", "criteria": ["AC-2"],
                   "stderr": "Traceback (most recent call last):\n" + message + "\n"}
            evidence = red_defect_context(manifest, {"product": {"stories": []}}, [row],
                                          "MANIFEST_OR_SOURCE_BOUNDARY")
            causes.append(evidence["blockers"][0]["mechanism"])
        self.assertEqual(causes, ["ImportError: missing alpha", "ImportError: missing beta"])

    def test_new_round_limit_is_accepted_by_project_settings(self):
        from storyloop.baseline import _bootstrap_settings

        settings, budget, roles = _bootstrap_settings({"acceptance_auto_retry_round_limit": 3})
        self.assertEqual(settings, {})
        self.assertEqual(budget["acceptance_auto_retry_round_limit"], 3)
        self.assertEqual(roles, {})

    def run_sequence(self, sequence, *, config=None, state=None):
        from storyloop.acceptance_repair import retry_fixture_red

        state = state or {"repairs": [], "acceptance": sequence[0]["acceptance"]}
        calls = []
        remaining = iter(sequence[1:])

        def repair(*args, **kwargs):
            calls.append(kwargs)
            state["repairs"].append({"automatic": True})
            result = next(remaining)
            if "acceptance" in result:
                state["acceptance"] = result["acceptance"]
            return result

        def pause(project, current, status, **details):
            current.update(details)
            return {"status": status, **details, "stop": {"evidence": [str(details)]}}

        with patch("storyloop.acceptance_repair.load_project_config", return_value=config or {}), \
             patch("storyloop.acceptance_repair.load_json", return_value=state), \
             patch("storyloop.acceptance_repair.save_run"), \
             patch("storyloop.acceptance_repair.pause", side_effect=pause), \
             patch("storyloop.acceptance_repair._repair_once", side_effect=repair):
            result = retry_fixture_red(Path("/tmp"), "C", None, sequence[0])
        return result, state, calls

    def test_ac1_third_distinct_strength_block_is_repaired(self):
        sequence = [blocked("A"), blocked("B"), blocked("C"),
                    {"status": "ACCEPTANCE_REPAIRED"}]
        result, state, calls = self.run_sequence(sequence,
            config={"acceptance_fixture_retry_limit": 0})
        self.assertEqual(result["status"], "ACCEPTANCE_REPAIRED")
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(call["automatic"] for call in calls))
        self.assertEqual(len(state["acceptance_retry"]["rounds"]), 3)

    def test_ac2_identical_block_stops_with_stable_ids(self):
        result, state, calls = self.run_sequence([blocked("A"), blocked("A")])
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["status"], "ORACLE_DEFECT")
        self.assertEqual(result["acceptance"]["retry_stop"]["reason"], "no_progress")
        self.assertEqual(result["acceptance"]["retry_stop"]["finding_ids"], ["A"])
        self.assertTrue(state["acceptance_retry"]["exhausted"])

    def test_ac2_stop_evidence_names_no_progress_and_repeated_ids(self):
        from storyloop.stop_report import build_stop

        state = {"change_id": "C", "status": "ORACLE_DEFECT", "acceptance": {
            **blocked("A")["acceptance"], "retry_stop": {
                "reason": "no_progress", "finding_ids": ["A"], "round_limit": 10}}}
        evidence = build_stop(Path("/tmp"), state, "ORACLE_DEFECT", {})["evidence"]
        self.assertTrue(any("no_progress" in item for item in evidence))
        self.assertTrue(any("A" in item and "ID" in item for item in evidence))

    def test_ac3_partial_repair_keeps_unresolved_finding_id(self):
        result, state, calls = self.run_sequence([blocked("A", "B"), blocked("B"),
                                                  {"status": "ACCEPTANCE_REPAIRED"}])
        self.assertEqual(result["status"], "ACCEPTANCE_REPAIRED")
        self.assertEqual(len(calls), 2)
        self.assertEqual(state["acceptance_retry"]["rounds"][1]["finding_ids"], ["B"])

    def test_ac4_configured_total_round_limit(self):
        result, state, calls = self.run_sequence([blocked("A"), blocked("B"), blocked("C")],
            config={"acceptance_auto_retry_round_limit": 2})
        self.assertEqual(len(calls), 2)
        self.assertEqual(result["acceptance"]["retry_stop"]["reason"], "round_limit")
        self.assertEqual(state["acceptance_retry"]["round_limit"], 2)

    def test_default_total_round_limit_is_ten(self):
        sequence = [blocked(f"ID-{index}") for index in range(11)]
        result, _, calls = self.run_sequence(sequence)
        self.assertEqual(len(calls), 10)
        self.assertEqual(result["acceptance"]["retry_stop"], {
            "reason": "round_limit", "finding_ids": ["ID-10"], "round_limit": 10})

    def test_replaying_same_saved_round_does_not_increment(self):
        from storyloop.acceptance_progress import remember_round

        state = {"repairs": []}
        acceptance = blocked("A")["acceptance"]
        self.assertEqual(remember_round(state, acceptance)[0], None)
        self.assertEqual(remember_round(state, acceptance)[0], None)
        self.assertEqual(len(state["acceptance_retry"]["rounds"]), 1)

    def test_manual_repair_after_no_progress_can_resume_when_finding_changes(self):
        from storyloop.acceptance_progress import blocking_signatures

        old, _ = blocking_signatures(blocked("A")["acceptance"])
        state = {"repairs": [{"automatic": True}, {"automatic": False}],
                 "acceptance": blocked("B")["acceptance"],
                 "acceptance_retry": {"version": 1, "exhausted": True,
                                      "rounds": [{"repair_count": 1,
                                                  "signatures": old, "finding_ids": ["A"]}]}}
        result, _, calls = self.run_sequence([blocked("B"),
            {"status": "ACCEPTANCE_REPAIRED"}], state=state)
        self.assertEqual(result["status"], "ACCEPTANCE_REPAIRED")
        self.assertEqual(len(calls), 1)

    def test_stable_review_id_ignores_message_wording_drift(self):
        from storyloop.acceptance_progress import blocking_signatures

        first = blocked("A")["acceptance"]
        second = blocked("A")["acceptance"]
        second["precheck"]["findings"][0]["message"] = "rewritten explanation"
        second["precheck"]["findings"][0]["location"] = "acceptance/test.py:24"
        self.assertEqual(blocking_signatures(first), blocking_signatures(second))

    def test_ac5_fixture_limit_zero_still_disables_fixture_repair(self):
        result, _, calls = self.run_sequence([deterministic("CASE_OR_FIXTURE_ERROR", "missing")],
            config={"acceptance_fixture_retry_limit": 0})
        self.assertEqual(result["status"], "ORACLE_DEFECT")
        self.assertFalse(calls)

    def test_ac6_decision_needed_does_not_enter_automatic_retry(self):
        from storyloop.acceptance_repair import retry_fixture_red

        state = {"repairs": [], "acceptance": {"status": "NEEDS_INPUT"}}
        with patch("storyloop.acceptance_repair.load_project_config", return_value={}), \
             patch("storyloop.acceptance_repair.load_json", return_value=state), \
             patch("storyloop.acceptance_repair._repair_once") as repair:
            result = retry_fixture_red(Path("/tmp"), "C", None, {"status": "NEEDS_INPUT"})
        self.assertEqual(result["status"], "NEEDS_INPUT")
        repair.assert_not_called()
        self.assertFalse(state["repairs"])

    def test_ac9_each_deterministic_category_can_reach_third_repair(self):
        for category in ("MUST_HOLD_RED", "MANIFEST_OR_SOURCE_BOUNDARY", "UNVERIFIED_ENTRY_USE"):
            with self.subTest(category=category):
                sequence = [deterministic(category, "mechanism A"),
                            deterministic(category, "mechanism B"),
                            deterministic(category, "mechanism C"),
                            {"status": "ACCEPTANCE_REPAIRED"}]
                result, _, calls = self.run_sequence(sequence,
                    config={"acceptance_fixture_retry_limit": 0})
                self.assertEqual(result["status"], "ACCEPTANCE_REPAIRED")
                self.assertEqual(len(calls), 3)

    def test_cost_limit_is_not_swallowed(self):
        from storyloop.acceptance_repair import retry_fixture_red

        state = {"repairs": [], "acceptance": blocked("A")["acceptance"]}
        with patch("storyloop.acceptance_repair.load_project_config", return_value={}), \
             patch("storyloop.acceptance_repair.load_json", return_value=state), \
             patch("storyloop.acceptance_repair.save_run"), \
             patch("storyloop.acceptance_repair._repair_once",
                   side_effect=StoryLoopError("cost", code="COST_LIMIT_REACHED")):
            with self.assertRaises(StoryLoopError) as caught:
                retry_fixture_red(Path("/tmp"), "C", None, blocked("A"))
        self.assertEqual(caught.exception.code, "COST_LIMIT_REACHED")

    def test_ac8_successful_red_proof_has_no_automatic_retry(self):
        result, state, calls = self.run_sequence([{"status": "PASSED", "acceptance": {}}])
        self.assertEqual(result["status"], "PASSED")
        self.assertFalse(calls)
        self.assertNotIn("acceptance_retry", state)

    def test_old_state_without_retry_metadata_is_readable(self):
        result, state, calls = self.run_sequence([blocked("A"),
            {"status": "ACCEPTANCE_REPAIRED"}], state={"repairs": [],
            "acceptance": blocked("A")["acceptance"]})
        self.assertEqual(result["status"], "ACCEPTANCE_REPAIRED")
        self.assertEqual(len(calls), 1)
        self.assertIn("acceptance_retry", state)


if __name__ == "__main__":
    unittest.main()
