"""Runtime compatibility for approved plans persisted before forbidden-path normalization."""

from copy import deepcopy
import unittest

from storyloop.routing_upgrade import enforce_forbidden_paths


class LegacyForbiddenPathTests(unittest.TestCase):
    def test_persisted_recursive_root_blocks_without_changing_plan_identity_or_approval(self):
        state = {'planning': {'plan': {
            'id': 'persisted-plan-id',
            'status': 'DONE',
            'main_option': 'main',
            'approval': {
                'actor': 'user', 'plan_id': 'persisted-plan-id', 'option_id': 'main',
                'binding': {'source_commit': 'abc123'},
            },
            'options': [{'id': 'main', 'forbidden_paths': ['tests/**']}],
        }}}
        before = deepcopy(state)
        boundary = {'status': 'ALLOWED', 'allowed': [
            {'path': 'tests/test_forbidden.py', 'category': 'TEST'},
        ], 'violations': []}

        enforce_forbidden_paths(state, boundary)

        self.assertEqual(boundary['status'], 'BOUNDARY_VIOLATION')
        self.assertEqual(boundary['allowed'], [])
        self.assertEqual(boundary['violations'], [
            {'path': 'tests/test_forbidden.py', 'category': 'PLAN_FORBIDDEN'},
        ])
        self.assertEqual(state, before)


if __name__ == '__main__':
    unittest.main()
