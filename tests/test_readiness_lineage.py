"""A verified receipt can carry existing readiness, never create missing trust."""
import tempfile
import unittest
from pathlib import Path
from storyloop.readiness import inspect_readiness, approve_readiness, PATH
from storyloop.readiness_lineage import advance_readiness
from storyloop.storage import load_json
from tests.routing_support import readiness_repository
from tests.test_baseline_behavior import git


class ReadinessLineageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        readiness_repository(self.root)
        record = inspect_readiness(self.root)
        self.previous = approve_readiness(self.root, record['digest'], 'local checks only')
        self.source = git(self.root, 'rev-parse', 'HEAD').strip()
        (self.root / 'app/new.py').write_text('value = 1\n')
        git(self.root, 'add', 'app/new.py')
        git(self.root, 'commit', '-qm', 'candidate')
        self.head = git(self.root, 'rev-parse', 'HEAD').strip()
        self.layers = {'unit': {'status': 'PASSED', 'results': [
            {'status': 'PASS', 'returncode': 0, 'timed_out': False,
             'counts': {'executed': 1, 'failed': 0, 'skipped': 0}}]},
            'integration': {'status': 'SKIPPED'}, 'checks': {'status': 'SKIPPED'},
            'smoke': {'status': 'SKIPPED'}}

    def test_receipt_is_mandatory_and_label_is_not_execution_evidence(self):
        state = {'source_head': self.source, 'integration_commit': self.head, 'status': 'PASSED'}
        self.assertIsNone(advance_readiness(self.root, state))
        self.assertEqual(load_json(self.root / PATH)['digest'], self.previous['digest'])

    def test_external_commit_invalidates_cache(self):
        record = inspect_readiness(self.root)
        self.assertEqual(record['status'], 'READINESS_APPROVAL_REQUIRED')
        self.assertNotEqual(record['digest'], self.previous['digest'])


class ReceiptIntegrationTests(unittest.TestCase):
    def test_real_completion_preserves_original_human_decision_and_advances(self):
        from storyloop.changes import create_change
        from storyloop.approvals import approve_change
        from storyloop.project_loop import run_change, load_run_state
        from tests.test_intake import assessed_product
        from tests.test_completion_resilience import _services
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            readiness_repository(root, module_roots=['app', 'modules'])
            ready = inspect_readiness(root)
            original = approve_readiness(root, ready['digest'], 'Local tests')
            value = assessed_product()
            value['assessment']['scope'] = {'touched': [], 'creates': ['modules/orders/service.py'],
                                            'symbols': [], 'interfaces': []}
            change = create_change(root, 'orders', value)
            identifier = change['change_id']
            approve_change(root, identifier)
            from unittest.mock import patch
            captured = []
            def advance(project, state):
                captured.append(dict(state))
                return advance_readiness(project, state)
            with patch('storyloop.readiness_lineage.advance_readiness', side_effect=advance):
                result = run_change(root, identifier, _services(identifier))
            self.assertEqual(result['status'], 'PASSED', result)
            current = inspect_readiness(root)
            self.assertEqual(current['status'], 'READY')
            self.assertEqual(current['approval'], original['approval'])
            self.assertNotEqual(current['digest'], original['digest'])
            self.assertEqual(current['lineage']['previous_digest'], original['digest'])
            self.assertEqual(current['lineage']['receipt_commit'], load_run_state(root, identifier)['integration_commit'])
            self.assertGreater(current['layers']['unit']['executed'], 0)
            self._refuses_untrusted_or_forged(root, captured[0], ready, original)

    def _refuses_untrusted_or_forged(self, root, state, unapproved, original):
        from storyloop.storage import write_json
        write_json(root / PATH, unapproved)
        self.assertIsNone(advance_readiness(root, state))
        self.assertEqual(load_json(root / PATH), unapproved)
        write_json(root / PATH, original)
        report = Path(state['report'])
        report.write_text('{"status": "PASSED"}')
        self.assertIsNone(advance_readiness(root, state))
        self.assertEqual(load_json(root / PATH), original)

    def test_partial_unknown_or_failed_results_cannot_form_certificate(self):
        from copy import deepcopy
        from storyloop.readiness_lineage import _layers
        from storyloop.baseline import load_project_config
        from storyloop.models import StoryLoopError
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            readiness_repository(root)
            config = load_project_config(root)
            unit = {'status': 'PASSED', 'results': [
                {'status': 'PASS', 'returncode': 0, 'timed_out': False,
                 'counts': {'executed': 1, 'failed': 0},
                 'command': [__import__('sys').executable, '-m', 'unittest', 'discover', '-s', 'tests']}]}
            base = {'unit': unit, **{name: {'status': 'SKIPPED', 'results': []}
                                      for name in ('integration', 'checks', 'smoke')}}
            mutations = [lambda x: x['unit'].update(results=[]),
                         lambda x: x['unit']['results'][0]['counts'].update(executed=None),
                         lambda x: x['unit']['results'][0]['counts'].update(executed=0),
                         lambda x: x['unit']['results'][0]['counts'].update(failed=1),
                         lambda x: x['unit']['results'][0].update(returncode=1),
                         lambda x: x['unit']['results'][0].update(command=['false'])]
            for mutate in mutations:
                candidate = deepcopy(base)
                mutate(candidate)
                with self.assertRaises(StoryLoopError):
                    _layers(root, config, candidate)
            config['profile']['unit_commands'] *= 2
            with self.assertRaises(StoryLoopError):
                _layers(root, config, base)
