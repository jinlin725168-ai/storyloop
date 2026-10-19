"""Typed positive routing assertions require real locations, never prose inference."""
from pathlib import Path
import tempfile
import unittest

from storyloop.models import StoryLoopError
from storyloop.project_contracts import validate_agent_summary, validate_review
from storyloop.role_schemas import schema_for, schema_errors
from tests.routing_support import routing_repository


class RiskTests(unittest.TestCase):
    def test_routed_schema_and_semantics_accept_typed_optional_updates(self):
        item = {'risk': 'migration', 'statement': 'Persisted data shape changes',
                'location': 'app/service.py:1'}
        response = {'summary': 'done', 'questions': [], 'routing_risks': [item]}
        self.assertEqual(schema_errors(response, schema_for('implement', {'routing_tier': 'light'})), [])
        validate_agent_summary(response)
        review = {**response, 'verdict': 'pass', 'findings': []}
        self.assertEqual(validate_review(review), 'pass')
        self.assertNotIn('routing_risks', schema_for('implement', {})['properties'])

    def test_location_and_shape_checked_atomically_and_provenance_retained(self):
        from storyloop.routing_risks import merge_routing_risks
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            routing_repository(root)
            state = {'attempt': 2, 'routing': {'version': 1}}
            valid = {'risk': 'external_contract', 'statement': 'Public callers change',
                     'location': 'app/service.py:1'}
            for bad in ({**valid, 'risk': 'made_up'}, {**valid, 'location': '../secret:1'},
                        {**valid, 'location': 'app/service.py:999'}, {**valid, 'statement': ''}):
                with self.subTest(bad=bad), self.assertRaises(StoryLoopError):
                    merge_routing_risks(root, state, [valid, bad], 'implement')
                self.assertNotIn('risk_updates', state['routing'])
            merge_routing_risks(root, state, [valid], 'review')
            merge_routing_risks(root, state, [valid], 'review')
            self.assertEqual(len(state['routing']['risk_updates']), 1)
            self.assertEqual(state['routing']['risk_updates'][0]['attempt'], 2)
            self.assertEqual(state['routing']['risk_updates'][0]['role'], 'review')

    def test_l3_cached_skipped_lock_cannot_call_missing_service(self):
        from types import SimpleNamespace
        from storyloop.project_loop_gates import ensure_characterization
        state = {'routing': {'version': 1, 'effective_tier': 'heavy'},
                 'planning': {'origin': 'L3', 'plan': {
                     'status': 'DONE', 'id': 'plan', 'main_option': 'main', 'design': 'locks',
                     'options': [{'id': 'main', 'touched': ['app/service.py']}]}},
                 'characterization': {'status': 'SKIPPED', 'touched': [], 'tests': []}}
        from unittest.mock import patch
        with patch('storyloop.project_loop_gates.characterize_paths',
                   side_effect=AssertionError('Missing service must not enter writer')), \
                self.assertRaisesRegex(StoryLoopError, 'characterization'):
            ensure_characterization(Path('/tmp'), {'product': {}}, state,
                                    SimpleNamespace(characterize=None), {'profile': {'test_roots': ['tests']}})
