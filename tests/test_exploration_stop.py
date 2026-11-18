"""A draft whose goal is not verifiable stops at EXPLORATION_RECOMMENDED (D1 gap).

Seen in every real L3 run (sqlite-utils #478-#481); before 2026-11-17 only the predicate
exploration_required was tested, never the stop a person actually sees.
"""
from pathlib import Path
import tempfile
import unittest

from storyloop.changes import create_change
from storyloop.readiness import approve_readiness, inspect_readiness
from storyloop.routing_report import draft_stop
from tests.routing_support import readiness_repository
from tests.test_intake import assessed_product


class ExplorationStopTests(unittest.TestCase):
    def test_unverifiable_goal_stops_with_a_revise_option(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            readiness_repository(root)
            ready = inspect_readiness(root)
            approve_readiness(root, ready['digest'], 'Local tests cover this change')
            product = assessed_product()
            product['assessment']['verifiable_goal'] = False
            change = create_change(root, 'vague idea', product)
            self.assertEqual(change['intake']['status'], 'EXPLORATION_RECOMMENDED')
            stop = draft_stop(root, change)
        self.assertEqual(stop['status'], 'EXPLORATION_RECOMMENDED')
        self.assertIn('storyloop change', stop['options'][0]['command'])
        self.assertIn('--message', stop['options'][0]['command'])


if __name__ == '__main__':
    unittest.main()
