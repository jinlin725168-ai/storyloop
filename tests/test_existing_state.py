"""A fix touching saved state must say whether state saved before it is covered (2026-11-21).

Guardrail bench, dogfood6: the contract left it open, the design role chose the option that skips
stops saved before the fix, and the users the fix was for kept the bug.
"""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from storyloop.changes import write_change_preview
from storyloop.requirement_checklist import evaluate_checklist
from tests.test_change_nature import fix_product

TEXT = "The fix decides whether data or state saved before it is covered"


def persistent(value="yes"):
    product = fix_product()
    product.setdefault("assessment", {}).setdefault("risks", {})["persistent_state"] = {"value": value}
    return product


def item(product, new_contract=True):
    found = [entry for entry in evaluate_checklist(product, require_observation=new_contract)
             if entry["text"].startswith(TEXT)]
    return found[0] if found else None


class ExistingStateTests(unittest.TestCase):
    def test_a_new_fix_contract_touching_saved_state_must_decide_and_a_person_owns_it(self):
        blocked = item(persistent())
        self.assertEqual((blocked["level"], blocked["dimension"]), ("error", "Clarity"))
        decided = persistent()
        decided["defect"]["existing_state"] = {"decision": "cover", "reason": "users already have stuck runs"}
        self.assertEqual(item(decided)["level"], "ok")

    def test_frozen_contracts_and_fixes_without_saved_state_are_unaffected(self):
        self.assertIsNone(item(persistent(), new_contract=False))  # bench Cases keep their checklist
        self.assertIsNone(item(persistent("no")))

    def test_the_draft_shows_the_decision(self):
        product = persistent()
        product["defect"]["existing_state"] = {"decision": "exclude", "reason": "only new runs are affected"}
        with tempfile.TemporaryDirectory() as folder:
            write_change_preview(Path(folder), {"change_id": "FIX-1", "product": deepcopy(product)})
            text = (Path(folder) / ".storyloop/drafts/FIX-1/proposal.md").read_text(encoding="utf-8")
        self.assertIn("## 修复前已有的数据或状态", text)
        self.assertIn("不覆盖：only new runs are affected", text)

    def test_the_product_schema_and_contract_accept_only_cover_or_exclude(self):
        from storyloop.models import StoryLoopError
        from storyloop.product_contract import validate_product
        from storyloop.role_schemas import DEFECT
        product = fix_product()
        product["defect"]["existing_state"] = {"decision": "cover", "reason": "r"}
        validate_product(product)
        product["defect"]["existing_state"]["decision"] = "maybe"
        with self.assertRaises(StoryLoopError):
            validate_product(product)
        self.assertIn("existing_state", DEFECT["properties"])


if __name__ == "__main__":
    unittest.main()
