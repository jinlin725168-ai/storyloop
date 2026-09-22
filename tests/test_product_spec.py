"""Product contract follows spec-kit structure: priorities, recommended options, clarifications on record."""

from __future__ import annotations

from datetime import datetime, timezone
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from storyloop.baseline import bootstrap_project
from storyloop.changes import contract_digest, create_change, load_change, revise_change
from storyloop.cli import clarify_interactively
from storyloop.models import StoryLoopError, validate_product
from storyloop.project_agents import INSTRUCTIONS
from tests.support import product
from tests.test_baseline_behavior import repository

NOW = datetime(2026, 9, 21, tzinfo=timezone.utc)


def rich_product() -> dict:
    value = product()
    value["capability"] = "room-booking"
    value["stories"][0]["priority"] = "P1"
    value["stories"][0]["independent_test"] = "Reserve one room and see it blocked for others."
    value["questions"] = [{"id": "Q1", "text": "Can a booking be cancelled?",
                           "options": ["Yes, until it starts", "No"],
                           "recommended": "Yes, until it starts", "why": "Rooms get freed for others."}]
    return value


class ProductSpecTests(unittest.TestCase):
    def test_optional_spec_kit_fields_are_accepted_and_validated(self):
        validate_product(rich_product())
        bad = rich_product(); bad["stories"][0]["priority"] = "high"
        with self.assertRaises(StoryLoopError):
            validate_product(bad)
        bad = rich_product(); bad["questions"][0]["recommended"] = "Maybe"
        with self.assertRaises(StoryLoopError):
            validate_product(bad)
        bad = rich_product(); bad["clarifications"] = [{"question": "x"}]
        with self.assertRaises(StoryLoopError):
            validate_product(bad)

    def test_clarifications_are_recorded_in_the_contract_and_preview(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            bootstrap_project(root)
            state = create_change(root, "booking", rich_product(), now=lambda: NOW)
            before = contract_digest(state["product"])
            answered = rich_product(); answered["questions"] = []
            revised = revise_change(root, state["change_id"], answered, "answers",
                                    clarifications=[{"question": "Can a booking be cancelled?",
                                                     "answer": "Yes, until it starts"}], now=lambda: NOW)
            entry = revised["product"]["clarifications"][0]
            self.assertEqual(entry["session"], "2026-09-21")
            self.assertEqual(entry["answer"], "Yes, until it starts")
            self.assertNotEqual(contract_digest(revised["product"]), before)
            proposal = (root / ".storyloop/drafts" / state["change_id"] / "proposal.md").read_text(encoding="utf-8")
            self.assertIn("## 澄清记录", proposal)
            self.assertIn("Can a booking be cancelled? → Yes, until it starts", proposal)
            self.assertIn("P1", proposal)
            # a later revision without new answers keeps the record
            again = revise_change(root, state["change_id"], answered, "tweak", now=lambda: NOW)
            self.assertEqual(len(again["product"]["clarifications"]), 1)

    def test_interactive_clarification_asks_at_most_five_and_records_answers(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            bootstrap_project(root)
            value = rich_product()
            value["questions"] = [{"id": f"Q{i}", "text": f"Question {i}?", "options": ["A", "B"],
                                   "recommended": "B", "why": "because"} for i in range(1, 8)]
            state = create_change(root, "booking", value, now=lambda: NOW)
            seen_messages = []

            def reviser(project, change_id, message, answers):
                seen_messages.append((message, answers))
                current = load_change(project, change_id)
                answered_ids = {a["question_id"] for a in answers}
                nxt = dict(current["product"])
                nxt["questions"] = [q for q in current["product"]["questions"] if q["id"] not in answered_ids]
                return revise_change(project, change_id, nxt, message, clarifications=[
                    {"question": a["question"], "answer": a["answer"]} for a in answers], now=lambda: NOW)

            replies = iter(["1", "", "B", "", "1", "", ""])  # round 1: five answers; round 2: two answers
            with patch("builtins.input", lambda prompt="": next(replies)), \
                    patch("storyloop.cli._terminal", return_value=True), \
                    patch("sys.stdout", new_callable=io.StringIO):
                final = clarify_interactively(root, state["change_id"], reviser, interactive=True)
            self.assertEqual(final["product"]["questions"], [])
            self.assertEqual(len(seen_messages), 2)
            self.assertEqual(len(seen_messages[0][1]), 5)
            self.assertEqual(seen_messages[0][1][0]["answer"], "A")      # "1" picks the first option
            self.assertEqual(seen_messages[0][1][1]["answer"], "B")      # empty picks the recommended
            self.assertIn("Q1", seen_messages[0][0])
            self.assertEqual(len(final["product"]["clarifications"]), 7)

    def test_non_interactive_leaves_questions_open(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            bootstrap_project(root)
            state = create_change(root, "booking", rich_product(), now=lambda: NOW)
            with patch("sys.stdin.isatty", return_value=False):
                final = clarify_interactively(root, state["change_id"], lambda *a: None, interactive=True)
            self.assertEqual(len(final["product"]["questions"]), 1)

    def test_product_instructions_carry_the_spec_discipline(self):
        text = INSTRUCTIONS["product"]
        for token in ("EARS", "SHALL", "Given", "5", "recommended", "priority", "P1", "clarifications",
                      "API", "core flow", "at most three"):
            self.assertIn(token, text)


if __name__ == "__main__":
    unittest.main()
