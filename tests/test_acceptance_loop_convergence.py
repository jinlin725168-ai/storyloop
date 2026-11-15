"""Executable precheck repairs and contract-bounded consistency findings."""

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from storyloop.consistency import validate_consistency
from storyloop.models import StoryLoopError
from storyloop.oracle_strength_precheck import assess
from tests.test_oracle_strength_precheck import CHANGE, finding
from tests.test_routing_planning import answer as plan_answer


def repair_change():
    return {"input": "nested artifact in reader mode", "assertion": "compare complete result",
            "expected": "nested artifact is retained"}


def criterion_spec():
    return {"inputs": "nested artifact", "preconditions": "reader mode",
            "entry": "VE-1 public command", "expected": "artifact retained",
            "negative_boundary": "root artifact only is insufficient",
            "contract_plan": "AC-1 via VE-1"}


def precheck_answer(*, new_id=False, full=False):
    row = finding("MUST_FIX")
    if new_id:
        row["id"] = "ORACLE-A02"
    row["required_change"] = repair_change()
    if full:
        row["criterion_spec"] = criterion_spec()
    return {"verdict": "fail", "summary": "nested path missing",
            "findings": [row], "questions": []}


class AcceptanceLoopConvergenceTests(unittest.TestCase):
    def assess(self, responses, state=None):
        requests = []

        def review(_project, context):
            requests.append(context)
            return responses.pop(0)

        state = state or {"routing": {"effective_tier": "standard"}}
        service = SimpleNamespace(review=review)
        change = deepcopy(CHANGE)
        change["product"]["stories"][0]["acceptance"] = [{"id": "AC-1", "entry_ids": ["VE-1"]}]
        with patch("storyloop.smoke.smoke_digests", return_value={}), \
             patch("storyloop.oracle_strength_precheck.read_only_call", side_effect=lambda p, c, fn: fn()):
            with self.assertRaises(StoryLoopError) as caught:
                assess(Path("/tmp"), change, state, service, {"tests": []}, {})
        return caught.exception, requests

    def test_must_fix_missing_executable_fields_gets_semantic_retry(self):
        invalid = precheck_answer()
        invalid["findings"][0]["required_change"] = {"input": "nested artifact"}
        error, requests = self.assess([invalid, precheck_answer()])
        self.assertEqual(error.code, "ORACLE_DEFECT")
        self.assertEqual(len(requests), 2)
        self.assertIn("required_change", requests[1]["validation_errors"][0])

    def test_precheck_requires_complete_spec_from_third_round(self):
        state = {"routing": {"effective_tier": "standard"},
                 "repairs": [{"automatic": True}, {"automatic": True,
                              "changed_paths": ["acceptance/test.py"]}],
                 "acceptance": {"precheck": {"findings": [finding("MUST_FIX")]}}}
        error, requests = self.assess([precheck_answer(), precheck_answer(full=True)], state)
        self.assertEqual(error.code, "ORACLE_DEFECT")
        self.assertEqual(len(requests), 2)
        self.assertIn("criterion_spec", requests[1]["validation_errors"][0])
        self.assertEqual(requests[0]["precheck_round"], 3)

    def test_new_third_round_finding_must_name_prior_edit(self):
        state = {"routing": {"effective_tier": "standard"},
                 "repairs": [{"automatic": True}, {"automatic": True,
                              "changed_paths": ["acceptance/test.py"]}],
                 "acceptance": {"precheck": {"findings": [finding("MUST_FIX")]}}}
        valid = precheck_answer(new_id=True, full=True)
        valid["findings"][0]["introduced_by"] = "acceptance/test.py:12 changed nested setup"
        error, requests = self.assess([precheck_answer(new_id=True, full=True), valid], state)
        self.assertEqual(error.code, "ORACLE_DEFECT")
        self.assertEqual(len(requests), 2)
        self.assertIn("introduced_by", requests[1]["validation_errors"][0])

    def test_new_finding_cannot_claim_unrelated_prior_edit(self):
        state = {"routing": {"effective_tier": "standard"},
                 "repairs": [{"automatic": True}, {"automatic": True,
                              "changed_paths": ["acceptance/test.py"]}],
                 "acceptance": {"precheck": {"findings": [finding("MUST_FIX")]}}}
        invalid = precheck_answer(new_id=True, full=True)
        invalid["findings"][0]["introduced_by"] = "acceptance/other.py:12 changed setup"
        valid = precheck_answer(new_id=True, full=True)
        valid["findings"][0]["introduced_by"] = "acceptance/test.py:12 changed setup"
        error, requests = self.assess([invalid, valid], state)
        self.assertEqual(error.code, "ORACLE_DEFECT")
        self.assertEqual(len(requests), 2)
        self.assertIn("prior repair", requests[1]["validation_errors"][0])

    def test_introduced_by_accepts_fullwidth_punctuation_after_line(self):
        # Self-iteration FIX-20261115-log-acceptance-rounds: the model wrote "path:80：reason";
        # requiring whitespace after the line number blocked the run.
        state = {"routing": {"effective_tier": "standard"},
                 "repairs": [{"automatic": True}, {"automatic": True,
                              "changed_paths": ["acceptance/test.py"]}],
                 "acceptance": {"precheck": {"findings": [finding("MUST_FIX")]}}}
        for source in ("acceptance/test.py:12：此前编辑改了夹具", "acceptance/test.py:12, changed setup",
                       "acceptance/test.py:12 changed setup"):
            with self.subTest(source=source):
                answer = precheck_answer(new_id=True, full=True)
                answer["findings"][0]["introduced_by"] = source
                error, requests = self.assess([answer], state)
                self.assertEqual(error.code, "ORACLE_DEFECT")
                self.assertEqual(len(requests), 1, requests[-1].get("validation_errors"))

    def test_prior_suite_edits_are_read_from_repair_commit(self):
        from storyloop.oracle_strength_precheck import _prior_suite_edits
        with patch("storyloop.baseline._git", return_value="acceptance/test.py\n") as git:
            paths = _prior_suite_edits(Path("/tmp"), {"repairs": [{"commit": "abc"}]})
        self.assertEqual(paths, ["acceptance/test.py"])
        git.assert_called_once_with(Path("/tmp"), "diff-tree", "--no-commit-id",
                                    "--name-only", "-r", "abc")

    def test_repair_request_lists_each_executable_change(self):
        from storyloop.acceptance_repair import repair_request
        state = {"acceptance": {"precheck": {"findings": [precheck_answer(full=True)["findings"][0]]}}}
        request = repair_request({"change_id": "C"}, state, "repair", [], {}, state["acceptance"])
        self.assertEqual(request["repair"]["required_changes"][0]["required_change"],
                         repair_change())
        self.assertEqual(request["repair"]["required_changes"][0]["id"], "ORACLE-A01")
        self.assertEqual(request["repair"]["required_changes"][0]["criterion_spec"],
                         criterion_spec())

    def test_plan_only_oracle_finding_becomes_advisory(self):
        item = precheck_answer()["findings"][0]
        item.update(location="acceptance/test.py:1", basis="plan_only",
                    basis_evidence="plan:main/constraints/0")
        response = {"verdict": "blocked", "summary": "plan-only edge case",
                    "questions": [], "findings": [item]}
        response["findings"][0]["target"] = "oracle"
        change = {"product": {"stories": [{"acceptance": [{"id": "AC-1"}]}]}}
        state = {"planning": {"plan": plan_answer()}, "contract_commit": "HEAD"}
        with patch("storyloop.consistency._validate_location"):
            result = validate_consistency(response, Path("/tmp"), change, state)
        self.assertEqual(result["verdict"], "coherent")
        self.assertEqual(result["findings"][0]["disposition"], "ADVISORY")
        self.assertFalse(result["findings"][0]["blocking"])

    def test_contract_based_oracle_finding_stays_blocking(self):
        item = precheck_answer()["findings"][0]
        item.update(location="acceptance/test.py:1", basis="contract",
                    basis_evidence="AC-1 then")
        response = {"verdict": "blocked", "summary": "contract violation",
                    "questions": [], "findings": [item]}
        change = {"product": {"stories": [{"acceptance": [{"id": "AC-1"}]}]}}
        state = {"planning": {"plan": plan_answer()}, "contract_commit": "HEAD"}
        with patch("storyloop.consistency._validate_location"):
            result = validate_consistency(response, Path("/tmp"), change, state)
        self.assertEqual(result["verdict"], "blocked")
        self.assertTrue(result["findings"][0]["blocking"])


if __name__ == "__main__":
    unittest.main()
