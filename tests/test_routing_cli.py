"""Routing commands are explicit decisions, with honest entry stops."""
import argparse
import io
import json
import shlex
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import patch

from storyloop.cli import dispatch, main, parser
from storyloop.models import StoryLoopError
from storyloop.routing_cli import validate_routing_args
from storyloop.stop_report import build_entry_stop, build_stop
from tests.routing_support import readiness_repository
from tests.test_intake import assessed_product


class RoutingCliTests(unittest.TestCase):
    def test_argument_matrix(self):
        base = dict(command='approve', change_id='ID', readiness=None, design=None,
                    continue_heavy=None, tier=None, option=None, reason=None)
        invalid = [dict(readiness='digest', reason='gap'), dict(tier='light'),
                   dict(reason='why'), dict(option='A'), dict(design='P', tier='heavy'),
                   dict(design='P', reason='why'), dict(continue_heavy='D', reason=' '),
                   dict(continue_heavy='D', reason='why', option='A'),
                   dict(command='run', replan=True, reason=''),
                   dict(command='run', replan=True, reason='why', overrule='no')]
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(StoryLoopError):
                validate_routing_args(argparse.Namespace(**{**base, **changes}))
        for changes in [dict(tier='heavy'), dict(design='P', option='A'),
                        dict(continue_heavy='D', reason='why'),
                        dict(readiness='D', change_id=None, reason='gap')]:
            validate_routing_args(argparse.Namespace(**{**base, **changes}))

    def test_dispatch_routing_leaves_legacy_commands_to_original_dispatch(self):
        from storyloop.routing_cli import dispatch_routing
        args = parser().parse_args(['approve', '/tmp/project', '--change', 'ID'])
        self.assertIsNone(dispatch_routing(Path('/tmp/project'), args))

    def test_entry_stop_has_no_fictional_run(self):
        project = Path('/tmp/a b')
        stop = build_entry_stop(project, 'READINESS_APPROVAL_REQUIRED', {'digest': 'abc'})
        self.assertFalse({'change_id', 'run', 'attempt', 'attempts'} & stop.keys())
        # str(project) is native: '\\tmp\\a b' on Windows.
        self.assertEqual(shlex.split(stop['resume'])[:5],
                         ['storyloop', 'approve', str(project), '--readiness', 'abc'])

    def test_design_selection_split_is_returned_without_running(self):
        args = parser().parse_args(['approve', '/tmp/a b', '--change', 'ID', '--design', 'P', '--option', 'B'])
        with patch('storyloop.routing_cli.approve_design', return_value={'status': 'SPLIT_RECOMMENDED'}) as approval:
            with patch('storyloop.cli.run_with_answers') as run:
                self.assertEqual(dispatch(args)['status'], 'SPLIT_RECOMMENDED')
                run.assert_not_called()
                approval.assert_called_once()

    def test_readiness_json_exit_codes_and_no_product_before_ready(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            readiness_repository(root)
            stream = io.StringIO()
            with redirect_stdout(stream):
                self.assertEqual(main(['readiness', folder, '--json']), 2)
            record = json.loads(stream.getvalue())
            self.assertEqual(record['status'], 'READINESS_APPROVAL_REQUIRED')
            error = io.StringIO()
            with redirect_stderr(error), redirect_stdout(io.StringIO()):
                self.assertEqual(main(['change', folder, '--idea', 'idea', '--json']), 2)
            stopped = json.loads(error.getvalue())
            self.assertEqual(stopped['next'], stopped['stop']['resume'])
            self.assertIn(record['digest'], stopped['next'])
            self.assertNotIn('attempts', stopped['stop'])

            with patch('storyloop.cli.invoke_project_agent') as agent:
                with self.assertRaises(StoryLoopError):
                    dispatch(parser().parse_args(['change', folder, '--idea', 'idea']))
                agent.assert_not_called()
            result = dispatch(parser().parse_args(['approve', folder, '--readiness', record['digest'], '--reason', 'local']))
            self.assertEqual(result['status'], 'READY')
            with patch('storyloop.cli.invoke_project_agent', return_value=assessed_product()) as agent:
                draft = dispatch(parser().parse_args(['change', folder, '--idea', 'idea', '--no-interactive']))
            self.assertEqual(draft['status'], 'DRAFT')
            payload = agent.call_args.args[2]
            self.assertEqual(payload['routing_version'], 1)
            self.assertTrue(payload['source_commit'])

    def test_planning_stop_quotes_exact_design_id(self):
        state = {'change_id': 'ID', 'phase': 'planning',
                 'planning': {'phase': 'APPROVAL', 'plan': {'id': 'P'}}, 'routing': {}}
        stop = build_stop('/tmp/a b', state, 'DESIGN_APPROVAL_REQUIRED', {})
        self.assertEqual(shlex.split(stop['resume']),
                         ['storyloop', 'approve', '/tmp/a b', '--change', 'ID', '--design', 'P'])
        self.assertEqual(stop['phase'], 'APPROVAL')


class RoutingReportTests(unittest.TestCase):
    def test_report_distinguishes_active_restore_and_retired_history(self):
        from storyloop.routing_report import routing_view, routing_markdown
        state = {'routing': {'initial_tier': 'light', 'effective_tier': 'heavy',
                            'budget_source': 'tier', 'limit': 4, 'reasons': [{'rule_id': 'X'}]},
                 'planning': {'phase': 'REPLAN', 'restore': {'phase': 'PENDING'},
                              'restore_history': [{'phase': 'DONE', 'retired_attempt': 1}]}}
        view = routing_view(state)
        self.assertEqual(view['restore']['phase'], 'PENDING')
        self.assertEqual(view['restore_history'][0]['retired_attempt'], 1)
        self.assertIn('heavy', '\n'.join(routing_markdown(view)))

    def test_status_is_readonly_and_exposes_draft_intake_stop(self):
        from storyloop.changes import create_change
        from tests.support import product
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            readiness_repository(root)
            draft = create_change(root, 'idea', product())
            before = {p: p.read_bytes() for p in (root / '.storyloop/state').rglob('*.json')}
            with patch('storyloop.readiness.inspect_readiness', side_effect=AssertionError('readonly')):
                result = dispatch(parser().parse_args(['status', folder, '--change', draft['change_id']]))
            self.assertEqual(result['stop']['status'], 'NEEDS_INPUT')
            self.assertFalse({'run', 'attempt', 'attempts'} & result['stop'].keys())
            self.assertEqual(before, {p: p.read_bytes() for p in before})


class ProductBoundaryTests(unittest.TestCase):
    def test_missing_assessment_repairs_once_and_writes_no_draft(self):
        from storyloop.cli import _product_answer
        from tests.support import product
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            readiness_repository(root)
            calls = []
            def agent(*args):
                calls.append(args[2])
                return product()
            with patch('storyloop.cli.invoke_project_agent', side_effect=agent), self.assertRaises(StoryLoopError):
                _product_answer(root, {'idea': 'idea'})
            self.assertEqual(len(calls), 2)
            self.assertIn('validation_errors', calls[1])
            self.assertFalse(list((root / '.storyloop/state/changes').glob('*.json')))

    def test_revision_and_repair_cannot_write_source(self):
        from storyloop.cli import _product_answer
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            readiness_repository(root)
            def agent(*args):
                (root / 'app/service.py').write_text('changed = True\n')
                return assessed_product()
            with patch('storyloop.cli.invoke_project_agent', side_effect=agent), self.assertRaises(StoryLoopError) as raised:
                _product_answer(root, {'change_id': 'ID', 'message': 'revision'})
            self.assertEqual(raised.exception.code, 'WORKSPACE_TAINTED')

    def test_scratch_cannot_hide_readiness_certificate_forgery(self):
        from storyloop.cli import _product_answer
        from storyloop.readiness import inspect_readiness, PATH
        from storyloop.storage import write_json
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            readiness_repository(root, scratch_paths=['.storyloop/state/readiness.json'])
            inspect_readiness(root)
            def agent(*args):
                write_json(root / PATH, {'status': 'READY'})
                return assessed_product()
            with patch('storyloop.cli.invoke_project_agent', side_effect=agent), self.assertRaises(StoryLoopError) as raised:
                _product_answer(root, {'idea': 'idea'})
            self.assertEqual(raised.exception.code, 'WORKSPACE_TAINTED')

    def test_illegal_reason_without_replan_even_if_blank(self):
        with self.assertRaises(StoryLoopError):
            validate_routing_args(argparse.Namespace(command='run', reason='', replan=False))

    def test_entry_blocked_draft_exposes_stop_on_change(self):
        from storyloop.readiness import inspect_readiness, approve_readiness
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            readiness_repository(root)
            ready = inspect_readiness(root)
            approve_readiness(root, ready['digest'], 'local')
            value = assessed_product()
            value['assessment']['criteria'][0]['existence'] = 'unknown'
            with patch('storyloop.cli.invoke_project_agent', return_value=value):
                result = dispatch(parser().parse_args(['change', folder, '--idea', 'idea', '--no-interactive']))
            self.assertEqual(result['status'], 'DRAFT')
            self.assertEqual(result['stop']['status'], 'NEEDS_INPUT')
            self.assertIn('--message', result['next'])


class ExplicitApprovalIntegrationTests(unittest.TestCase):
    from tests.test_design_approval import DesignApprovalTests as _fixture
    setUp = _fixture.setUp
    start = _fixture.start

    def test_cli_run_never_approves_then_explicit_cli_approval_continues(self):
        from storyloop.project_loop import run_change, load_run_state
        self.start()
        self.assertEqual(self.state['phase'], 'planning')
        self.assertNotIn('phase', self.state['planning'])
        self.assertEqual(self.state['stop']['phase'], 'planning')
        with patch('storyloop.cli.run_change', side_effect=lambda p, c: run_change(p, c, self.services)):
            result = dispatch(parser().parse_args(['run', str(self.root), '--change', self.identifier, '--no-interactive']))
        self.assertEqual(result['status'], 'DESIGN_APPROVAL_REQUIRED')
        self.assertNotIn('approval', load_run_state(self.root, self.identifier)['planning']['plan'])
        self.assertIn(self.plan['id'], result['next'])
        snapshot = dispatch(parser().parse_args(['status', str(self.root), '--change', self.identifier]))
        self.assertEqual(snapshot['status'], 'DESIGN_APPROVAL_REQUIRED')

        def continued(project, change_id):
            state = load_run_state(project, change_id)
            self.assertEqual(state['planning']['plan']['approval']['actor'], 'user')
            return {'status': 'NEEDS_INPUT'}
        with patch('storyloop.cli.run_change', side_effect=continued):
            result = dispatch(parser().parse_args(['approve', str(self.root), '--change', self.identifier,
                                                    '--design', self.plan['id'], '--no-interactive']))
        self.assertEqual(result['status'], 'NEEDS_INPUT')
        self.assertEqual(self.calls, [])
        with self.assertRaises(StoryLoopError):
            dispatch(parser().parse_args(['approve', str(self.root), '--change', self.identifier,
                                          '--design', 'stale', '--no-interactive']))
        with patch('storyloop.cli.run_change', side_effect=lambda p, c: run_change(p, c, self.services)):
            result = dispatch(parser().parse_args(['run', str(self.root), '--change', self.identifier,
                                                    '--replan', '--reason', 'Revise plan', '--no-interactive']))
        self.assertEqual(result['status'], 'DESIGN_APPROVAL_REQUIRED')
        state = load_run_state(self.root, self.identifier)
        self.assertNotIn('approval', state['planning']['plan'])
        self.assertEqual(state['attempt'], 0)
