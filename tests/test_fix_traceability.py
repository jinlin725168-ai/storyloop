"""Fix traceability (roadmap D3): a fix names the change that introduced it and why it escaped.

Before 2026-11-17 `defect.origin_change` was optional free text the product role had to know, and
nothing recorded which gate missed the defect, so escapes could not point at what to strengthen.
"""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from storyloop.models import StoryLoopError
from storyloop.product_contract import ESCAPE_CLASSES, validate_product
from storyloop.requirement_checklist import evaluate_checklist
from tests.test_baseline_behavior import git
from tests.test_change_nature import fix_product


def _items(product, project=None):
    return {item["text"]: item["level"] for item in evaluate_checklist(product, project)}


class EscapeClassContractTests(unittest.TestCase):
    def test_escape_class_is_one_of_the_known_gates(self):
        value = fix_product()
        value["defect"].update(origin_change="FEAT-20261101-orders", escape_class="oracle")
        validate_product(value)
        self.assertIn("characterization", ESCAPE_CLASSES)
        value["defect"]["escape_class"] = "luck"
        with self.assertRaisesRegex(StoryLoopError, "escape_class"):
            validate_product(value)

    def test_a_named_origin_needs_its_escape_class(self):
        value = fix_product()
        value["defect"]["origin_change"] = "FEAT-20261101-orders"
        # Warning, so contracts from before D3 (e.g. frozen bench cases) still approve.
        self.assertTrue(any(level == "warning" and "escape_class" in text
                            for text, level in _items(value).items()))
        value["defect"]["escape_class"] = "review"
        self.assertFalse(any("escape_class" in text and level != "ok"
                             for text, level in _items(value).items()))


class OriginCandidateTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        git(self.root, "init", "-q")
        git(self.root, "config", "user.email", "storyloop@example.invalid")
        git(self.root, "config", "user.name", "StoryLoop Test")
        target = self.root / "modules/orders/service.py"
        target.parent.mkdir(parents=True)
        target.write_text("PAGE = 0\n", encoding="utf-8")
        git(self.root, "add", ".")
        git(self.root, "commit", "-qm", "feat(FEAT-20261101-orders): implement approved product change")

    def test_root_cause_line_names_the_storyloop_change_that_last_touched_it(self):
        value = fix_product()
        warned = [text for text, level in _items(value, self.root).items()
                  if level == "warning" and "FEAT-20261101-orders" in text]
        self.assertTrue(warned, "a candidate origin change is named while origin_change is empty")
        named = deepcopy(value)
        named["defect"].update(origin_change="FEAT-20261101-orders", escape_class="oracle")
        self.assertFalse([text for text, level in _items(named, self.root).items()
                          if level == "warning" and "FEAT-20261101-orders" in text])
        preexisting = deepcopy(value)
        preexisting["defect"]["escape_class"] = "preexisting"
        self.assertFalse([text for text, level in _items(preexisting, self.root).items()
                          if level == "warning" and "FEAT-20261101-orders" in text])


class EscapeMetricTests(unittest.TestCase):
    def test_escapes_are_counted_by_class(self):
        from storyloop.metrics_compute import escapes
        records = [
            {"change_id": "FEAT-1", "status": "PASSED", "nature": "feature", "tier_effective": "standard"},
            {"change_id": "FIX-1", "status": "PASSED", "nature": "fix", "tier_effective": "light",
             "origin_change": "FEAT-1", "escape_class": "oracle"}]
        self.assertEqual(escapes(records)["by_escape_class"], {"oracle": 1})


if __name__ == "__main__":
    unittest.main()
