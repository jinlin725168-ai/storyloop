"""Routed heavy must prove behavior locks before the implement role can run."""
import unittest
from unittest.mock import Mock

from storyloop.models import StoryLoopError
from storyloop.project_loop import LoopServices, load_run_state, run_change
from tests import test_routing_state
from tests.test_routing_planning import answer
from tests.test_project_loop import smoke_payload
from tests.test_structured_output import passing_review


class RoutedLockTests(unittest.TestCase):
    # Reuse real readiness, approval, and heavy run fixture.
    setUp = test_routing_state.RunRoutingTests.setUp

    def services(self, characterize):
        implement = Mock(side_effect=AssertionError('implement must not run'))
        return LoopServices(lambda p, c: smoke_payload(self.identifier), implement,
                            passing_review, characterize=characterize,
                            design=lambda p, c: answer())

    def approve(self, services):
        from storyloop.design_approval import approve_design
        paused = run_change(self.root, self.identifier, services)
        self.assertEqual(paused['status'], 'DESIGN_APPROVAL_REQUIRED')
        approve_design(self.root, self.identifier, paused['plan_id'])

    def test_characterization_receives_behavior_to_change_and_instruction_to_avoid_it(self):
        from storyloop.role_instructions import INSTRUCTIONS
        observed = {}
        def characterize(project, context):
            observed.update(context)
            return {'summary': 'No lock', 'touched': [], 'tests': [], 'questions': []}
        services = self.services(characterize)
        self.approve(services)
        with self.assertRaises(StoryLoopError):
            run_change(self.root, self.identifier, services)
        self.assertTrue(observed['changing_behaviors'])
        self.assertIn('不得锁定', INSTRUCTIONS['characterize'])
        self.assertIn('特征化缺陷', INSTRUCTIONS['review'])

    def test_empty_characterization_cannot_claim_planned_paths(self):
        services = self.services(lambda p, c: {
            'summary': 'No locks', 'touched': [], 'tests': [], 'questions': []})
        self.approve(services)
        with self.assertRaisesRegex(StoryLoopError, 'coverage|artifact'):
            run_change(self.root, self.identifier, services)
        services.implement.assert_not_called()
        state = load_run_state(self.root, self.identifier)
        self.assertNotEqual(state.get('characterization', {}).get('status'), 'GREEN')

    def test_declared_coverage_without_test_artifacts_is_rejected(self):
        services = self.services(lambda p, c: {
            'summary': 'Claimed lock', 'touched': ['app/service.py'],
            'tests': ['tests/missing.py'], 'questions': []})
        self.approve(services)
        with self.assertRaisesRegex(StoryLoopError, 'artifact'):
            run_change(self.root, self.identifier, services)
        services.implement.assert_not_called()

    def test_test_artifacts_without_coverage_declaration_are_rejected(self):
        def characterize(project, context):
            (project / 'tests/test_lock.py').write_text(
                'import unittest\nfrom app.service import serve\n'
                'class Lock(unittest.TestCase):\n'
                '    def test_serve(self):\n        self.assertEqual(serve(), "ok")\n')
            return {'summary': 'Undeclared', 'touched': [],
                    'tests': ['tests/test_lock.py'], 'questions': []}
        services = self.services(characterize)
        self.approve(services)
        with self.assertRaisesRegex(StoryLoopError, 'coverage'):
            run_change(self.root, self.identifier, services)
        services.implement.assert_not_called()


class LockEvidenceTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        from tests.routing_support import routing_repository
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        routing_repository(self.root)
        self.config = {'profile': {'test_roots': ['tests']}}
        self.pending = {'touched': ['app/service.py'], 'coverage': {
            'app/service.py': ['tests/test_service.py']}}
        from storyloop.characterization_locks import capture_locks
        self.state = {'characterization': {'status': 'GREEN', 'touched': ['app/service.py'],
                      'locks': capture_locks(self.root, self.config, self.pending)}}

    def test_valid_existing_locks_can_be_reused_without_rewriting_tests(self):
        from storyloop.characterization_locks import routed_coverage, require_saved_coverage
        response = {'touched': [], 'tests': []}
        coverage = routed_coverage(self.root, self.state, self.config, response, [], ['app/service.py'])
        self.assertEqual(coverage, self.pending['coverage'])
        require_saved_coverage(self.root, self.state, self.config, ['app/service.py'])

    def test_changed_lock_artifact_cannot_be_reused(self):
        from storyloop.characterization_locks import require_saved_coverage
        (self.root / 'tests/test_service.py').write_text('changed\n')
        with self.assertRaisesRegex(StoryLoopError, 'changed since GREEN'):
            require_saved_coverage(self.root, self.state, self.config, ['app/service.py'])

    def test_deleted_artifact_cannot_be_green_on_resume(self):
        from storyloop.characterization_locks import capture_locks
        (self.root / 'tests/test_service.py').unlink()
        with self.assertRaisesRegex(StoryLoopError, 'artifact is missing'):
            capture_locks(self.root, self.config, self.pending)

    def test_green_without_evidence_does_not_satisfy_required_paths(self):
        from storyloop.characterization_locks import require_saved_coverage
        self.state['characterization'].pop('locks')
        with self.assertRaisesRegex(StoryLoopError, 'coverage evidence'):
            require_saved_coverage(self.root, self.state, self.config, ['app/service.py'])

    def test_unrelated_existing_file_cannot_invent_new_lock(self):
        from storyloop.characterization_locks import routed_coverage
        response = {'touched': ['app/service.py'], 'tests': ['tests/test_service.py']}
        with self.assertRaisesRegex(StoryLoopError, 'produced'):
            routed_coverage(self.root, {}, self.config, response, [], ['app/service.py'])

    def test_artifact_paths_reject_links_and_escape(self):
        from storyloop.characterization_locks import capture_locks
        (self.root / 'tests/linked.py').symlink_to('test_service.py')
        for path in ('tests/linked.py', '../outside.py'):
            with self.subTest(path=path), self.assertRaises(StoryLoopError):
                capture_locks(self.root, self.config, {'touched': ['app/service.py'],
                              'coverage': {'app/service.py': [path]}})
