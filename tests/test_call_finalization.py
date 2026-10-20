"""Diagnostic failures cannot change adapter outcomes or leave call records open."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from storyloop.call_log import load_meta, open_call
from storyloop.models import StoryLoopError
from storyloop.project_agents import _execute_call
from storyloop.telemetry import ledger_entries


class CallFinalizationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.call = open_call(self.root, 'implement', 'FEAT-1', 'command', {})

    def execute(self):
        trace = dict(stderr='', sessions=[], raw=None, repairs=0, schema_errors=[],
                     instructions='', skills=[], telemetry=None)
        return _execute_call(self.root, 'implement', {}, {}, {}, {'kind': 'command'},
                             {'adapter': 'command'}, None, None, 'FEAT-1', self.call, trace)

    def test_memory_failure_preserves_original_error_and_closes_record(self):
        original = StoryLoopError('original adapter failure', code='AGENT_UNAVAILABLE')
        with patch('storyloop.project_agents._command', side_effect=original), \
                patch('storyloop.project_agents._record_memory_evidence', side_effect=OSError('diagnostic')):
            with self.assertRaises(StoryLoopError) as raised:
                self.execute()
        self.assertIs(raised.exception, original)
        self.assertTrue(ledger_entries(self.root, 'FEAT-1')[0]['failed'])
        self.assertEqual(load_meta(self.root, self.call)['status'], 'FAILED')
        self.assertEqual(load_meta(self.root, self.call)['code'], 'AGENT_UNAVAILABLE')

    def test_each_diagnostic_is_independent_and_success_survives(self):
        for failing in ('_record_memory_evidence', 'record_call', 'close_call'):
            self.call = open_call(self.root, 'implement', 'FEAT-1', 'command', {})
            with self.subTest(failing=failing), \
                    patch('storyloop.project_agents._command', return_value={'summary': 'ok'}), \
                    patch('storyloop.project_agents.' + failing, side_effect=OSError('diagnostic')), \
                    patch('storyloop.errata.capture_errata', side_effect=OSError('errata')) as capture:
                self.assertEqual(self.execute(), {'summary': 'ok'})
                capture.assert_called_once()
                if failing != 'close_call':
                    self.assertEqual(load_meta(self.root, self.call)['status'], 'OK')
                if failing != 'record_call':
                    self.assertIn(self.call, [row['call'] for row in ledger_entries(self.root, 'FEAT-1')])

    def test_errata_failure_keeps_success_and_closed_record(self):
        with patch('storyloop.project_agents._command', return_value={'summary': 'ok'}), \
                patch('storyloop.errata.capture_errata', side_effect=ValueError('errata')):
            self.assertEqual(self.execute(), {'summary': 'ok'})
        self.assertEqual(load_meta(self.root, self.call)['status'], 'OK')
        self.assertTrue(ledger_entries(self.root, 'FEAT-1'))
