"""Crash-safe selective restores retain other candidate bytes and reject drift."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from storyloop.models import StoryLoopError
from storyloop.storage import load_json
from tests.routing_support import routing_repository
from tests.test_baseline_behavior import git


class RestoreTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        routing_repository(self.root)
        self.state = {'change_id': 'FEAT-restore', 'attempt': 2,
                      'contract_commit': git(self.root, 'rev-parse', 'HEAD').strip(),
                      'planning': {'origin': 'L3', 'phase': 'REPLAN', 'consumed_attempt': 2}}
        self.source = self.root / 'app/service.py'
        self.before = self.source.read_bytes()
        self.source.write_text('candidate = 1\n')
        self.retained = self.root / 'app/new.py'
        self.retained.write_bytes(b'keep exact bytes\n')

    def test_selective_restore_is_idempotent(self):
        from storyloop.routing_restore import prepare_restore, resume_restore
        prepare_restore(self.root, self.state, ['app/service.py'])
        saved = load_json(self.root / '.storyloop/state/runs/FEAT-restore.json')
        self.assertNotIn('candidate =', str(saved))
        self.assertEqual(saved['planning']['restore']['phase'], 'PENDING')
        resume_restore(self.root, saved)
        self.assertEqual(self.source.read_bytes(), self.before)
        self.assertEqual(self.retained.read_bytes(), b'keep exact bytes\n')
        resume_restore(self.root, saved)
        self.assertEqual(saved['attempt'], 2)

    def test_user_edit_after_journal_is_never_overwritten(self):
        from storyloop.routing_restore import prepare_restore, resume_restore
        prepare_restore(self.root, self.state, ['app/service.py'])
        self.source.write_text('user = 2\n')
        with self.assertRaisesRegex(StoryLoopError, 'changed|conflict'):
            resume_restore(self.root, self.state)
        self.assertEqual(self.source.read_text(), 'user = 2\n')

    def test_crash_after_checkout_recognizes_contract_fingerprint(self):
        from storyloop.routing_restore import prepare_restore, resume_restore
        prepare_restore(self.root, self.state, ['app/service.py'])
        with patch('storyloop.routing_restore.save_run', side_effect=RuntimeError('crash')):
            with self.assertRaises(RuntimeError):
                resume_restore(self.root, self.state)
        saved = load_json(self.root / '.storyloop/state/runs/FEAT-restore.json')
        resume_restore(self.root, saved)
        self.assertEqual(self.source.read_bytes(), self.before)
        self.assertEqual(saved['planning']['restore']['phase'], 'DONE')


class L3ResumeTests(unittest.TestCase):
    def setUp(self):
        from tests.test_routing_upgrade import L3IntegrationTests
        L3IntegrationTests.setUp(self)

    def services(self):
        from tests.test_routing_upgrade import L3IntegrationTests
        return L3IntegrationTests.services(self)

    def open_approved_l3(self):
        from dataclasses import replace
        from storyloop.design_approval import approve_design
        from storyloop.project_loop import run_change
        services = self.services()
        paused = run_change(self.root, self.identifier, services)
        approve_design(self.root, self.identifier, paused['plan_id'])
        calls = []
        def characterize(project, context):
            calls.append(context)
            (project / 'tests/test_lock.py').write_text(
                'import unittest\nfrom app.service import serve\n'
                'class Lock(unittest.TestCase):\n'
                '    def test_serve(self): self.assertEqual(serve(), "ok")\n')
            return {'summary': 'lock', 'touched': ['app/service.py'],
                    'tests': ['tests/test_lock.py'], 'questions': []}
        return replace(services, characterize=characterize), calls

    def test_crash_after_characterization_rebinds_once_without_repeating_lock(self):
        from storyloop.project_loop import run_change, load_run_state
        from storyloop.design_approval import approve_design
        services, calls = self.open_approved_l3()
        with patch('storyloop.routing_execution.rebind_after_locks', side_effect=RuntimeError('crash')):
            with self.assertRaisesRegex(RuntimeError, 'crash'):
                run_change(self.root, self.identifier, services)
        state = load_run_state(self.root, self.identifier)
        self.assertEqual(state['planning']['phase'], 'CHARACTERIZE')
        result = run_change(self.root, self.identifier, services)
        self.assertEqual(result['status'], 'DESIGN_APPROVAL_REQUIRED')
        approve_design(self.root, self.identifier, result['plan_id'])
        self.assertEqual(run_change(self.root, self.identifier, services)['status'], 'PASSED')
        self.assertEqual(len(calls), 1)
        self.assertEqual(load_run_state(self.root, self.identifier)['attempt'], 2)

    def test_user_candidate_edit_after_characterization_crash_cannot_be_rebound(self):
        from storyloop.project_loop import run_change, load_run_state
        services, calls = self.open_approved_l3()
        with patch('storyloop.routing_execution.rebind_after_locks', side_effect=RuntimeError('crash')):
            with self.assertRaises(RuntimeError):
                run_change(self.root, self.identifier, services)
        target = self.root / 'modules/orders/service.py'
        target.write_text('user = 2\n')
        with self.assertRaises(StoryLoopError):
            run_change(self.root, self.identifier, services)
        self.assertEqual(target.read_text(), 'user = 2\n')
        self.assertEqual(load_run_state(self.root, self.identifier)['attempt'], 1)

    def test_crash_before_restore_reenters_real_run_and_retains_other_candidate(self):
        from dataclasses import replace
        from storyloop.project_loop import run_change, load_run_state
        services = self.services()
        original = services.implement
        def implement(project, context):
            response = original(project, context)
            (project / 'app/service.py').write_text('def serve():\n    return "candidate"\n')
            return response
        services = replace(services, implement=implement)
        with patch('storyloop.routing_execution.resume_restore', side_effect=RuntimeError('crash')):
            with self.assertRaises(RuntimeError):
                run_change(self.root, self.identifier, services)
        saved = load_run_state(self.root, self.identifier)
        self.assertEqual(saved['planning']['restore']['phase'], 'PENDING')
        self.assertEqual(saved['planning']['consumed_attempt'], 1)
        retained = (self.root / 'modules/orders/service.py').read_bytes()
        self.assertEqual(run_change(self.root, self.identifier, services)['status'], 'DESIGN_APPROVAL_REQUIRED')
        self.assertEqual((self.root / 'app/service.py').read_text(), "def serve():\n    return 'ok'\n")
        self.assertEqual((self.root / 'modules/orders/service.py').read_bytes(), retained)
        self.assertEqual(load_run_state(self.root, self.identifier)['failures'], [])

    def test_planning_agent_unavailable_never_refunds_consumed_implement(self):
        from dataclasses import replace
        from storyloop.project_loop import run_change, load_run_state
        from tests.test_agent_unavailable import unavailable
        services = self.services()
        original = services.design
        def design(project, context):
            if context.get('replanning', {}).get('origin') == 'L3':
                raise unavailable()
            return original(project, context)
        with self.assertRaises(StoryLoopError) as caught:
            run_change(self.root, self.identifier, replace(services, design=design))
        self.assertEqual(caught.exception.code, 'AGENT_UNAVAILABLE')
        state = load_run_state(self.root, self.identifier)
        self.assertEqual(state['attempt'], 1)
        self.assertEqual(state['planning']['consumed_attempt'], 1)
        self.assertEqual(state['failures'], [])
        self.assertEqual(run_change(self.root, self.identifier, services)['status'], 'DESIGN_APPROVAL_REQUIRED')
        self.assertEqual(load_run_state(self.root, self.identifier)['attempt'], 1)

    def test_tainted_planning_reset_preserves_consumed_attempt(self):
        from dataclasses import replace
        from storyloop.project_loop import run_change, load_run_state
        from storyloop.run_recovery import reset_run
        services = self.services()
        original = services.design
        def design(project, context):
            if context.get('replanning', {}).get('origin') == 'L3':
                (project / 'modules/orders/service.py').write_text('polluted = True\n')
            return original(project, context)
        with self.assertRaises(StoryLoopError) as caught:
            run_change(self.root, self.identifier, replace(services, design=design))
        self.assertEqual(caught.exception.code, 'WORKSPACE_TAINTED')
        self.assertEqual(reset_run(self.root, self.identifier, 'Repaired planning edits')['status'],
                         'DESIGN_REVISION_REQUIRED')
        self.assertEqual(load_run_state(self.root, self.identifier)['attempt'], 1)
        self.assertEqual(run_change(self.root, self.identifier, services)['status'], 'DESIGN_APPROVAL_REQUIRED')
        self.assertEqual(load_run_state(self.root, self.identifier)['attempt'], 1)

    def test_consistency_phase_crash_resumes_without_consuming_an_attempt(self):
        from storyloop.project_loop import run_change, load_run_state
        services, calls = self.open_approved_l3()
        with patch('storyloop.routing_execution.ensure_consistency', side_effect=RuntimeError('crash')):
            with self.assertRaises(RuntimeError):
                run_change(self.root, self.identifier, services)
        self.assertEqual(load_run_state(self.root, self.identifier)['planning']['phase'], 'CONSISTENCY')
        self.assertEqual(run_change(self.root, self.identifier, services)['status'], 'DESIGN_APPROVAL_REQUIRED')
        self.assertEqual(load_run_state(self.root, self.identifier)['attempt'], 1)
        self.assertEqual(len(calls), 1)

    def test_ready_checkpoint_rejects_unexpected_user_edit(self):
        from storyloop.design_approval import approve_design
        from storyloop.project_loop import run_change, load_run_state
        services, calls = self.open_approved_l3()
        paused = run_change(self.root, self.identifier, services)
        approve_design(self.root, self.identifier, paused['plan_id'])
        with patch('storyloop.project_loop._attempts', side_effect=RuntimeError('crash')):
            with self.assertRaises(RuntimeError):
                run_change(self.root, self.identifier, services)
        state = load_run_state(self.root, self.identifier)
        self.assertEqual(state['planning']['phase'], 'READY')
        self.assertEqual(state['attempt'], 1)
        (self.root / 'modules/orders/service.py').write_text('user = 3\n')
        self.assertEqual(run_change(self.root, self.identifier, services)['status'], 'DESIGN_REVISION_REQUIRED')
        self.assertEqual(load_run_state(self.root, self.identifier)['attempt'], 1)
        self.assertEqual(len(calls), 1)
        from storyloop.design_approval import replan
        locks = load_run_state(self.root, self.identifier)['characterization']['locks']
        replan(self.root, self.identifier, 'Review the deliberate candidate change')
        replanned = load_run_state(self.root, self.identifier)
        self.assertEqual(replanned['characterization']['locks'], locks)
        self.assertEqual(replanned['attempt'], 1)
        self.assertEqual(run_change(self.root, self.identifier, services)['status'], 'DESIGN_APPROVAL_REQUIRED')
        self.assertEqual(len(calls), 1)
