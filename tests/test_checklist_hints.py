"""Draft checklist names what blocks approval and the form that would pass."""

import unittest

from storyloop.requirement_checklist import blocking_items, evaluate_checklist
from tests.test_intake import assessed_product
from tests.test_output_contract import _new_output_product


class ChecklistHintTests(unittest.TestCase):
    def test_unknown_risk_is_visible_in_the_draft_review(self):
        # 2026-11-15 self-iteration E: risks.guard was unknown with no question; the draft
        # checklist passed and only approve stopped at NEEDS_INPUT. The stop stays with intake
        # (test_routing_cli: approve, run and status report the same NEEDS_INPUT).
        value = assessed_product()
        value["assessment"]["risks"]["guard"]["value"] = "unknown"
        items = evaluate_checklist(value)
        flagged = [item for item in items if "risks.guard" in item["text"]]
        self.assertEqual([item["level"] for item in flagged], ["warning"])
        self.assertFalse([item for item in blocking_items(items) if "risks." in item["text"]])
        value["assessment"]["risks"]["guard"]["value"] = "no"
        self.assertFalse([item for item in evaluate_checklist(value) if "risks." in item["text"]])

    def test_untyped_array_names_the_accepted_forms(self):
        # 2026-11-16 self-iteration E: "needs an element type" took two revisions to fix.
        value = _new_output_product()
        value["stories"][0]["acceptance"][0]["output_contract"] = {
            "location": "VE-ENTRY: JSON", "fields": [
                {"name": "events", "type": "array", "meaning": "时间线"}]}
        errors = blocking_items(evaluate_checklist(value, require_output_contract=True))
        text = next(item["text"] for item in errors if "element type" in item["text"])
        self.assertIn("events", text)
        self.assertIn("array<object>", text)
        self.assertIn("events[].", text)


if __name__ == "__main__":
    unittest.main()
