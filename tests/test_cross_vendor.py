import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from storyloop.adapter_capabilities import check
from storyloop.baseline import load_project_config
from storyloop.models import StoryLoopError
from storyloop.project_agents import invoke_project_agent
from storyloop.storage import write_json
from storyloop.telemetry import ledger_entries
from storyloop.stop_report import build_stop
from tests.test_structured_output import command_project


def configured(root, policy='fail_closed'):
    command_project(root, 'print("{}")')
    config = load_project_config(root)
    adapter = {'kind': 'command', 'agent_command': config['agent_command']}
    config.update(adapters={'a': adapter, 'b': adapter},
                  roles={'default_adapter': 'a', 'review': {'adapter': 'b'}},
                  cross_vendor_review={'enabled': True, 'on_unavailable': policy})
    write_json(root / '.storyloop/config.json', config)
    return config


def capabilities(vendor):
    return {'known': True, 'tiers': ['strong', 'standard'], 'vendor': vendor,
            'subagents': False, 'path_guard': False, 'fast_checks': False, 'modes': ['*']}


class CrossVendorTests(unittest.TestCase):
    def test_check_rejects_same_vendor(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            configured(root)
            with patch('storyloop.adapter_capabilities.probe', return_value=capabilities('same')):
                with self.assertRaises(StoryLoopError):
                    check(root)

    def test_unavailable_fail_closed_has_cross_vendor_and_ledger(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            configured(root)
            with patch('storyloop.adapter_capabilities.probe', side_effect=[capabilities('a'), capabilities('b')]), \
                 patch('storyloop.project_agents._command', side_effect=StoryLoopError('offline', code='AGENT_UNAVAILABLE')):
                with self.assertRaises(StoryLoopError) as caught:
                    invoke_project_agent(root, 'review', {'change_id': 'FEAT-1'})
            self.assertTrue(caught.exception.details['cross_vendor'])
            self.assertEqual(len(ledger_entries(root, 'FEAT-1')), 1)

    def test_degrade_logs_both_calls_and_report(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            configured(root, 'degrade')
            answer = {'verdict': 'pass', 'summary': 'ok', 'findings': [], 'questions': []}
            with patch('storyloop.adapter_capabilities.probe', side_effect=[capabilities('a'), capabilities('b')]), \
                 patch('storyloop.project_agents._command', side_effect=[StoryLoopError('offline', code='AGENT_UNAVAILABLE'), answer]):
                invoke_project_agent(root, 'review', {'change_id': 'FEAT-1'})
            entries = ledger_entries(root, 'FEAT-1')
            self.assertEqual(len(entries), 2)
            self.assertTrue(entries[0]['failed'])
            self.assertEqual(entries[1]['adapter'], 'a')
            self.assertEqual(entries[1]['degraded'], 'cross_vendor')
            self.assertNotIn('vendor', entries[1])
            stop = build_stop(root, {'change_id': 'FEAT-1'}, 'NEEDS_INPUT', {})
            self.assertEqual(stop['degraded'], 'cross_vendor')

    def test_fallback_checks_limit_after_failed_review(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config = configured(root, 'degrade')
            config['limits'] = {'max_calls': 1}
            write_json(root / '.storyloop/config.json', config)
            with patch('storyloop.adapter_capabilities.probe', side_effect=[capabilities('a'), capabilities('b')]), \
                 patch('storyloop.project_agents._command', side_effect=StoryLoopError('offline', code='AGENT_UNAVAILABLE')) as call:
                with self.assertRaises(StoryLoopError) as caught:
                    invoke_project_agent(root, 'review', {'change_id': 'FEAT-1'})
            self.assertEqual(caught.exception.code, 'COST_LIMIT_REACHED')
            self.assertEqual(call.call_count, 1)
            self.assertEqual(len(ledger_entries(root, 'FEAT-1')), 1)

    def test_unknown_handshake_obeys_policy_and_counts_failure(self):
        for policy in ('fail_closed', 'degrade'):
            with self.subTest(policy=policy), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                configured(root, policy)
                probes = [capabilities('a'), {'known': False, 'tiers': []}]
                answer = {'verdict': 'pass', 'summary': 'ok', 'findings': [], 'questions': []}
                with patch('storyloop.adapter_capabilities.probe', side_effect=probes), \
                     patch('storyloop.project_agents._command', return_value=answer) as call:
                    if policy == 'fail_closed':
                        with self.assertRaises(StoryLoopError) as caught:
                            invoke_project_agent(root, 'review', {'change_id': 'FEAT-1'})
                        self.assertTrue(caught.exception.details['cross_vendor'])
                        call.assert_not_called()
                    else:
                        invoke_project_agent(root, 'review', {'change_id': 'FEAT-1'})
                        call.assert_called_once()
                entries = ledger_entries(root, 'FEAT-1')
                self.assertTrue(entries[0]['failed'])
                self.assertEqual(len(entries), 1 if policy == 'fail_closed' else 2)

    def test_invalid_availability_policy_fails_before_call(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            configured(root, 'silently_accept')
            with patch('storyloop.project_agents._command') as call:
                with self.assertRaisesRegex(StoryLoopError, 'on_unavailable'):
                    invoke_project_agent(root, 'review', {'change_id': 'FEAT-1'})
            call.assert_not_called()

    def test_fail_closed_returns_quality_attempt(self):
        from storyloop.project_loop import LoopServices, run_change, load_run_state
        from tests.test_structured_output import approved_change
        from tests.test_project_loop import smoke_payload
        from tests.test_agent_unavailable import implement_after, single_attempt
        temporary, root, change_id = approved_change()
        try:
            single_attempt(root)
            config = load_project_config(root)
            config.update(adapters={'a': {'kind': 'manual'}, 'b': {'kind': 'manual'}},
                          roles={'default_adapter': 'a', 'review': {'adapter': 'b'}},
                          cross_vendor_review={'enabled': True})
            write_json(root / '.storyloop/config.json', config)
            services = LoopServices(lambda p, c: smoke_payload(change_id), implement_after(0, []),
                                    lambda p, c: invoke_project_agent(p, 'review', c))
            with patch('storyloop.adapter_capabilities.probe', side_effect=[capabilities('a'), capabilities('b')]), \
                 patch('storyloop.project_agents._manual', side_effect=StoryLoopError('offline', code='AGENT_UNAVAILABLE')):
                with self.assertRaises(StoryLoopError) as caught:
                    run_change(root, change_id, services)
            self.assertEqual(caught.exception.code, 'AGENT_UNAVAILABLE', str(caught.exception))
            self.assertTrue(caught.exception.details['cross_vendor'])
            self.assertEqual(load_run_state(root, change_id)['attempt'], 0)
            self.assertTrue(ledger_entries(root, change_id)[0]['failed'])
        finally:
            temporary.cleanup()

    def test_enabled_cross_vendor_cannot_bypass_handshake_on_legacy_config(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            command_project(root, 'print("{}")')
            config = load_project_config(root)
            config['cross_vendor_review'] = {'enabled': True}
            write_json(root / '.storyloop/config.json', config)
            with self.assertRaises(StoryLoopError) as caught:
                check(root)
            self.assertTrue(caught.exception.details['cross_vendor'])

    def test_limits_resume_rewarms_cache_outside_read_only_guard(self):
        from storyloop.project_loop import LoopServices, run_change, load_run_state
        from tests.test_structured_output import approved_change, passing_review
        from tests.test_project_loop import smoke_payload
        from tests.test_agent_unavailable import implement_after
        temporary, root, change_id = approved_change()
        calls = []
        try:
            config = load_project_config(root)
            config.update(adapters={'a': {'kind': 'manual'}, 'b': {'kind': 'manual'}},
                          roles={'default_adapter': 'a', 'review': {'adapter': 'b'}},
                          cross_vendor_review={'enabled': True}, limits={'max_calls': 0}, max_attempts=1)
            write_json(root / '.storyloop/config.json', config)
            services = LoopServices(lambda p, c: smoke_payload(change_id), implement_after(0, calls),
                                    lambda p, c: invoke_project_agent(p, 'review', c))
            with patch('storyloop.adapter_capabilities.probe', side_effect=[capabilities('a'), capabilities('b')] * 2), \
                 patch('storyloop.project_agents._manual', return_value=passing_review(root, {})) as call:
                self.assertEqual(run_change(root, change_id, services)['status'], 'COST_LIMIT_REACHED')
                self.assertEqual(load_run_state(root, change_id)['attempt'], 0)
                config['limits']['max_calls'] = 1
                write_json(root / '.storyloop/config.json', config)
                self.assertEqual(run_change(root, change_id, services)['status'], 'PASSED')
            self.assertEqual(calls, [1])
            call.assert_called_once()
        finally:
            temporary.cleanup()
