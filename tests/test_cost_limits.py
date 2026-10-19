import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from storyloop.models import StoryLoopError
from storyloop.project_agents import invoke_project_agent
from storyloop.telemetry import record_call, ledger_entries
from storyloop.stop_report import build_stop, render_stop
from tests.test_structured_output import command_project, approved_change, passing_review
from tests.test_project_loop import smoke_payload
from tests.test_agent_unavailable import implement_after, single_attempt
from storyloop.project_loop import LoopServices, run_change, load_run_state
from storyloop.baseline import load_project_config
from storyloop.storage import write_json


class CostLimitsTests(unittest.TestCase):
    def test_default_stops_call_81_before_process(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            command_project(root, 'print("{}")')
            for index in range(80):
                record_call(root, 'review', 'FEAT-1', {}, 1, failed=True)
            with patch('storyloop.project_agents._command') as call:
                with self.assertRaises(StoryLoopError) as caught:
                    invoke_project_agent(root, 'review', {'change_id': 'FEAT-1'})
            self.assertEqual(caught.exception.code, 'COST_LIMIT_REACHED')
            call.assert_not_called()
            self.assertEqual(len(ledger_entries(root, 'FEAT-1')), 80)

    def test_dimensions_coverage_and_excluded_ledgers(self):
        from storyloop.cost_limits import check_before_call
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            record_call(root, 'review', 'FEAT-1', {'cost_usd': 2, 'input_tokens': 4}, 2000, failed=True)
            record_call(root, 'implement', 'FEAT-1', {}, 1000)
            record_call(root, 'survey', 'foundation', {}, 100000)
            record_call(root, 'product', None, {}, 100000)
            for limits in ({'max_calls': 2}, {'max_tokens': 4}, {'max_cost_usd': 2},
                           {'max_agent_wall_seconds': 3}):
                with self.assertRaises(StoryLoopError) as caught:
                    check_before_call(root, 'FEAT-1', {'limits': limits})
                details = caught.exception.details
                self.assertEqual(details['cost_coverage'], {'known': 1, 'total': 2})
                self.assertEqual(details['used']['calls'], 2)
                stop = build_stop(root, {'change_id': 'FEAT-1'}, 'COST_LIMIT_REACHED', details)
                self.assertEqual(stop['cost_coverage'], details['cost_coverage'])
                self.assertIn('1/2', '\n'.join(render_stop(stop)))
            check_before_call(root, None, {'limits': {'max_calls': 0}})
            check_before_call(root, 'foundation', {'limits': {'max_calls': 0}})

    def test_failed_process_counts_against_next_call(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            command_project(root, 'import sys; sys.exit(1)')
            config = load_project_config(root)
            config['limits'] = {'max_calls': 1}
            write_json(root / '.storyloop/config.json', config)
            for expected in ('AGENT_UNAVAILABLE', 'COST_LIMIT_REACHED'):
                with self.assertRaises(StoryLoopError) as caught:
                    invoke_project_agent(root, 'review', {'change_id': 'FEAT-1'})
                self.assertEqual(caught.exception.code, expected)


class CostResumeTests(unittest.TestCase):
    def test_review_stop_keeps_candidate_and_same_attempt(self):
        from storyloop.cost_limits import check_before_call
        temporary, root, change_id = approved_change()
        calls, reviews = [], []
        def review(project, context):
            reviews.append(context['attempt'])
            check_before_call(project, change_id, load_project_config(project))
            return passing_review(project, context)
        services = LoopServices(lambda p, c: smoke_payload(change_id), implement_after(0, calls), review)
        try:
            single_attempt(root)
            config = load_project_config(root)
            config['limits'] = {'max_calls': 0}
            write_json(root / '.storyloop/config.json', config)
            first = run_change(root, change_id, services)
            self.assertEqual(first['status'], 'COST_LIMIT_REACHED')
            self.assertEqual(load_run_state(root, change_id)['attempt'], 0)
            config['limits']['max_calls'] = 1
            write_json(root / '.storyloop/config.json', config)
            self.assertEqual(run_change(root, change_id, services)['status'], 'PASSED')
            self.assertEqual(calls, [1])
            self.assertEqual(reviews, [1, 1])
        finally:
            temporary.cleanup()

    def test_acceptance_cost_stop_resumes_preparation(self):
        from storyloop.cost_limits import check_before_call
        temporary, root, change_id = approved_change()
        def acceptance(project, context):
            check_before_call(project, change_id, load_project_config(project))
            return smoke_payload(change_id)
        services = LoopServices(acceptance, implement_after(0, []), passing_review)
        try:
            config = load_project_config(root)
            config['limits'] = {'max_calls': 0}
            write_json(root / '.storyloop/config.json', config)
            self.assertEqual(run_change(root, change_id, services)['status'], 'COST_LIMIT_REACHED')
            self.assertEqual(load_run_state(root, change_id)['attempt'], 0)
            config['limits']['max_calls'] = 10
            write_json(root / '.storyloop/config.json', config)
            self.assertEqual(run_change(root, change_id, services)['status'], 'PASSED')
        finally:
            temporary.cleanup()

    def test_design_cost_stop_resumes_without_consuming_attempt(self):
        from storyloop.cost_limits import check_before_call
        temporary, root, change_id = approved_change()
        designs, implementations = [], []
        def design(project, context):
            designs.append(1)
            check_before_call(project, change_id, load_project_config(project))
            return {'summary': 'plan', 'design': '# Plan', 'touched': [], 'questions': []}
        services = LoopServices(lambda p, c: smoke_payload(change_id),
                                implement_after(0, implementations), passing_review, design=design)
        try:
            config = load_project_config(root)
            config.update(limits={'max_calls': 0}, design_min_stories=1, max_attempts=1)
            write_json(root / '.storyloop/config.json', config)
            self.assertEqual(run_change(root, change_id, services)['status'], 'COST_LIMIT_REACHED')
            self.assertEqual(load_run_state(root, change_id)['attempt'], 0)
            self.assertEqual(implementations, [])
            config['limits']['max_calls'] = 5
            write_json(root / '.storyloop/config.json', config)
            self.assertEqual(run_change(root, change_id, services)['status'], 'PASSED')
            self.assertEqual(designs, [1, 1])
            self.assertEqual(implementations, [1])
        finally:
            temporary.cleanup()

    def test_modified_candidate_is_rejected_on_resume(self):
        from storyloop.cost_limits import check_before_call
        temporary, root, change_id = approved_change()
        calls = []
        def review(project, context):
            check_before_call(project, change_id, load_project_config(project))
            return passing_review(project, context)
        services = LoopServices(lambda p, c: smoke_payload(change_id), implement_after(0, calls), review)
        try:
            config = load_project_config(root)
            config['limits'] = {'max_calls': 0}
            write_json(root / '.storyloop/config.json', config)
            self.assertEqual(run_change(root, change_id, services)['status'], 'COST_LIMIT_REACHED')
            (root / 'modules/orders/service.py').write_text('VALUE = 999\n')
            config['limits']['max_calls'] = 5
            write_json(root / '.storyloop/config.json', config)
            with self.assertRaisesRegex(StoryLoopError, 'Workspace changed'):
                run_change(root, change_id, services)
            self.assertEqual(calls, [1])
        finally:
            temporary.cleanup()

    def test_late_characterization_checks_cost_before_restoring_candidate(self):
        from storyloop.project_loop import _judge_attempt
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config = {'limits': {'max_calls': 0},
                      'profile': {'test_roots': ['tests'], 'unit_commands': [['python', '-m', 'unittest']]}}
            services = LoopServices(None, None, None, characterize=lambda p, c: {})
            state = {'change_id': 'FEAT-1', 'attempt': 1, 'contract_commit': 'old'}
            response = {'summary': 'done', 'questions': []}
            boundary = {'violations': [], 'allowed': []}
            with patch('storyloop.project_loop.merge_facts'), \
                 patch('storyloop.project_loop.audit_project_changes', return_value=boundary), \
                 patch('storyloop.project_loop._risk_updates', return_value=None), \
                 patch('storyloop.project_loop.candidate_l3', return_value=(False, None)), \
                 patch('storyloop.project_loop.uncharacterized_paths', return_value=['modules/existing.py']), \
                 patch('storyloop.project_loop._git') as git:
                with self.assertRaises(StoryLoopError) as caught:
                    _judge_attempt(root, {'change_id': 'FEAT-1'}, state, services, config, response, [])
            self.assertEqual(caught.exception.code, 'COST_LIMIT_REACHED')
            git.assert_not_called()
