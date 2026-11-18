"""A draft's next step names every rule that will refuse approval (2026-11-17 dogfood15).

The draft hint listed only open questions and checklist items; "a new-behaviour row is partial"
was known at intake but surfaced only when approve refused, costing one more revision round.
"""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from storyloop.run_hints import draft_next
from tests.test_intake import assessed_product


class DraftNextReasonTests(unittest.TestCase):
    def test_intake_reasons_beyond_questions_are_named_before_approve(self):
        product = assessed_product()
        product['assessment']['criteria'][0]['existence'] = 'partial'
        change = {'change_id': 'FEAT-1', 'status': 'DRAFT', 'product': deepcopy(product),
                  'intake': {'status': 'NEEDS_INPUT', 'decision': {'unresolved': [], 'reasons': []}}}
        with tempfile.TemporaryDirectory() as folder:
            hint = draft_next(Path(folder), change)
        self.assertIn('existence 为 partial', hint)
        self.assertIn('approve 会被拒绝', hint)

    def test_a_ready_draft_still_suggests_approve(self):
        change = {'change_id': 'FEAT-1', 'status': 'DRAFT', 'product': assessed_product(),
                  'intake': {'status': 'READY', 'decision': {'unresolved': [], 'reasons': []}}}
        with tempfile.TemporaryDirectory() as folder:
            hint = draft_next(Path(folder), change)
        self.assertNotIn('approve 会被拒绝', hint)


if __name__ == '__main__':
    unittest.main()
