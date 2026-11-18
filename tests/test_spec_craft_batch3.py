"""spec-craft batch 3: assumptions, example polarity, rule links, assessment and draft handoff."""
from copy import deepcopy
from pathlib import Path
import json
import tempfile
import unittest

from storyloop.models import StoryLoopError
from storyloop.product_contract import validate_product
from storyloop.requirement_checklist import evaluate_checklist
from tests.support import product


def levels(value):
    return {item["text"]: item["level"] for item in evaluate_checklist(value)}


def linked():
    """Two rules, each with a positive and a negative example linked to it."""
    value = product()
    story = value["stories"][0]
    story["rules"] = ["The system SHALL reject an overlapping reservation", "The system SHALL accept a free slot"]
    base = story["acceptance"][0]
    story["acceptance"] = [
        {**base, "id": "AC-1", "rule": 1, "polarity": "negative",
         "given": "room \"A\" booked 10:00-11:00", "when": "booking 10:30-11:30", "then": "it is rejected"},
        {**base, "id": "AC-2", "rule": 1, "polarity": "positive",
         "given": "room \"A\" booked 10:00-11:00", "when": "booking 11:00-12:00", "then": "it is accepted"},
        {**base, "id": "AC-3", "rule": 2, "polarity": "positive",
         "given": "room \"B\" free all day", "when": "booking 09:00-10:00", "then": "it is accepted"}]
    return value


class ContractFieldTests(unittest.TestCase):
    def test_assumptions_polarity_and_assessment_reference_are_validated(self):
        value = linked()
        value["assumptions"] = [{"id": "A1", "statement": "Rooms are booked in local time", "status": "confirmed"}]
        value["assessment_ref"] = "ASSESS-20261118-rooms"
        validate_product(value)
        for mutate in (lambda v: v["assumptions"][0].update(status="maybe"),
                       lambda v: v["stories"][0]["acceptance"][0].update(polarity="sideways")):
            broken = deepcopy(value)
            mutate(broken)
            with self.subTest(), self.assertRaises(StoryLoopError):
                validate_product(broken)


class ChecklistTests(unittest.TestCase):
    def test_an_unconfirmed_assumption_blocks_approval(self):
        value = product()
        value["assumptions"] = [{"id": "A1", "statement": "Bookings are hourly", "status": "assumed"}]
        self.assertIn("error", [level for text, level in levels(value).items() if "A1" in text])
        value["assumptions"][0]["status"] = "confirmed"
        self.assertNotIn("error", [level for text, level in levels(value).items() if "A1" in text])

    def test_rule_linked_contracts_need_a_rule_per_example_and_a_positive_example_per_rule(self):
        self.assertFalse([t for t, level in levels(linked()).items() if level == "error" and "rule" in t.lower()])
        unlinked = linked()
        del unlinked["stories"][0]["acceptance"][2]["rule"]
        self.assertTrue([t for t, level in levels(unlinked).items() if level == "error" and "AC-3" in t])
        no_positive = linked()
        no_positive["stories"][0]["acceptance"][2]["polarity"] = "negative"
        self.assertTrue([t for t, level in levels(no_positive).items()
                         if level == "error" and "positive example" in t])
        # Rule 2 has no negative example: a warning, not a block (risk-proportional, roadmap B).
        self.assertIn("warning", [level for t, level in levels(linked()).items() if "negative example" in t])

    def test_old_contracts_get_no_new_errors(self):
        self.assertFalse([t for t, level in levels(product()).items()
                          if level == "error" and ("rule" in t.lower() or "assumption" in t.lower())])

    def test_examples_without_concrete_values_are_warned(self):
        value = product()  # "a reservation exists": no number, quote, date or ID
        self.assertIn("warning", [level for t, level in levels(value).items() if "concrete value" in t])
        self.assertNotIn("warning", [level for t, level in levels(linked()).items() if "concrete value" in t])


if __name__ == "__main__":
    unittest.main()


ANSWERS = ["--idea", "Book meeting rooms", "--problem", "On 2026-11-10 two teams double-booked room A",
           "--success", "No overlapping bookings in a week", "--if-not", "Teams keep losing meetings"]


class AssessTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        from tests.routing_support import readiness_repository
        readiness_repository(self.root)

    def assess(self, *extra):
        from storyloop.cli import dispatch, parser
        return dispatch(parser().parse_args(["assess", str(self.root), *ANSWERS, *extra]))

    def test_assess_records_the_three_answers_and_the_human_decision(self):
        from storyloop.cli import dispatch, parser
        result = dispatch(parser().parse_args(["assess", str(self.root), *ANSWERS, "--decision", "do"]))
        self.assertEqual(result["status"], "ASSESSED")
        self.assertTrue(result["assessment_id"].startswith("ASSESS-"))
        self.assertIn("--assessment " + result["assessment_id"], result["next"])
        from storyloop.assessments import load_assessment
        record = load_assessment(self.root, result["assessment_id"])
        self.assertEqual(record["decision"], "do")
        self.assertEqual(record["answers"]["problem"], "On 2026-11-10 two teams double-booked room A")

    def test_drop_creates_no_change_and_a_missing_answer_is_refused(self):
        from storyloop.cli import dispatch, parser
        result = dispatch(parser().parse_args(["assess", str(self.root), *ANSWERS, "--decision", "drop"]))
        self.assertEqual(result["decision"], "drop")
        self.assertFalse((self.root / ".storyloop/state/changes").exists()
                         and list((self.root / ".storyloop/state/changes").iterdir()))
        from storyloop.cli import dispatch, parser
        with self.assertRaisesRegex(StoryLoopError, "--success"):
            dispatch(parser().parse_args(["assess", str(self.root), "--idea", "x", "--problem", "p",
                                          "--if-not", "c", "--decision", "do", "--no-interactive"]))

    def test_only_a_do_decision_can_start_a_change(self):
        from storyloop.assessments import require_do
        dropped = self.assess("--decision", "drop")["assessment_id"]
        with self.assertRaisesRegex(StoryLoopError, "decision is drop"):
            require_do(self.root, dropped)
        self.assertEqual(require_do(self.root, self.assess("--decision", "do")["assessment_id"])["decision"], "do")


class HandoffTests(unittest.TestCase):
    """change --assessment and change --from-draft, with the product role scripted."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        from tests.routing_support import readiness_repository
        from storyloop.readiness import approve_readiness, inspect_readiness
        readiness_repository(self.root)
        approve_readiness(self.root, inspect_readiness(self.root)["digest"], "Local tests")

    def change(self, answer, **flags):
        from argparse import Namespace
        from storyloop.changes import load_change
        from storyloop.product_cli import change
        calls = []

        def product_answer(project, payload, call=None):
            calls.append(payload)
            return deepcopy(answer)

        args = Namespace(supersede=None, reason=None, idea=flags.get("idea"), change_id=None, message=None,
                         slug="rooms", no_interactive=True, assessment=flags.get("assessment"),
                         from_draft=flags.get("from_draft"))
        result = change(self.root, args, product_answer, None,
                        lambda p, c, r, i: load_change(p, c), lambda p, s, n=None: s,
                        lambda p: True, None)
        return result, calls

    def test_a_do_assessment_is_linked_and_lands_in_the_cocreation_record(self):
        from storyloop.assessments import create_assessment
        from tests.test_intake import assessed_product
        record = create_assessment(self.root, "Book rooms", {"problem": "p", "success": "s", "if_not": "c"}, "do")
        state, calls = self.change(assessed_product(), idea="Book rooms", assessment=record["id"])
        self.assertEqual(calls[0]["assessment_record"]["id"], record["id"])
        self.assertEqual(state["product"]["assessment_ref"], record["id"])
        from storyloop.approvals import cocreation_record
        approved = dict(state, approval={"approved_at": "2026-11-18T00:00:00+00:00",
                                         "versions": {"skills": [{"name": "spec-craft", "version": "1.4.7"}]}})
        cocreation = cocreation_record(self.root, approved)
        self.assertEqual((cocreation["entry"], cocreation["assessment"]["decision"]), ("cli", "do"))
        self.assertEqual(cocreation["spec_craft"], "1.4.7")

    def test_from_draft_keeps_the_human_confirmed_stories_and_records_the_agent_account(self):
        from tests.test_intake import assessed_product
        draft = assessed_product()
        draft.pop("assessment")
        draft["stories"][0]["rules"] = ["The system SHALL reject an overlapping reservation"]
        folder = self.root.parent / (self.root.name + "-draft")
        folder.mkdir()
        self.addCleanup(lambda: __import__("shutil").rmtree(folder, ignore_errors=True))
        (folder / "contract.json").write_text(json.dumps(draft), encoding="utf-8")
        (folder / "record.json").write_text(json.dumps({"questions": 3}), encoding="utf-8")
        rewritten = assessed_product()
        rewritten["stories"][0]["rules"] = ["The product role rewrote this"]
        state, calls = self.change(rewritten, from_draft=folder)
        self.assertIn("Keep its goal", calls[0]["message"])
        self.assertEqual(state["product"]["stories"][0]["rules"], draft["stories"][0]["rules"])
        self.assertIn("assessment", state["product"])  # routing assessment from the product role
        self.assertEqual(state["cocreation"], {"entry": "chat", "agent_record": {"questions": 3}, "source": "agent"})
