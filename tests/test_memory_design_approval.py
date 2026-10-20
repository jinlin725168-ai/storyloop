"""Lesson commits freeze into the next run without weakening source drift checks."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from storyloop.approvals import approve_change
from storyloop.baseline import load_baseline
from storyloop.changes import create_change
from storyloop.design_approval import approve_design
from storyloop.drafts import create_draft
from storyloop.lesson_landing import approve_draft
from storyloop.models import StoryLoopError
from storyloop.project_loop import load_run_state, run_change
from tests.lesson_support import commit_file, sample, write_lesson
from tests.routing_support import routed_acceptance, routed_case, routed_product
from tests.test_baseline_behavior import git
from tests.test_routing_planning import answer


class MemoryDesignApprovalTests(unittest.TestCase):
    def case(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        change, services = routed_case(root, 'heavy')
        return root, change['change_id'], services

    def test_approved_prose_before_second_heavy_can_approve_and_complete(self):
        root, first, services = self.case()
        paused = run_change(root, first, services)
        approve_design(root, first, paused['plan_id'])
        self.assertEqual(run_change(root, first, services)['status'], 'PASSED')
        entry = sample()
        entry.pop('id')
        body = entry.pop('body')
        payload = dict(action='create', target=None, entry=entry, why_needed='需要',
                       evidence=['人工确认'], landing=None)
        ref = 'lesson/' + create_draft(root, 'lesson', 'manual', None, payload, body)
        landed = approve_draft(root, ref)
        self.assertIsNone(landed.get('base_proposal'))
        self.assertNotEqual(load_baseline(root)['source_head'], landed['commit'])
        product = json.loads(json.dumps(routed_product()).replace('orders', 'invoices'))
        second = create_change(root, 'invoices', product)
        identifier = approve_change(root, second['change_id'], tier='heavy')['change_id']
        services = replace(services, design=lambda p, c: answer(product),
                           generate_smoke=lambda p, c: self.acceptance(identifier),
                           implement=self.implement)
        paused = run_change(root, identifier, services)
        self.assertEqual(load_run_state(root, identifier)['source_head'], landed['commit'])
        self.assertEqual(paused['status'], 'DESIGN_APPROVAL_REQUIRED', paused)
        approval = approve_design(root, identifier, paused['plan_id'])
        self.assertEqual(approval['plan_id'], paused['plan_id'])
        result = run_change(root, identifier, services)
        self.assertEqual(result['status'], 'PASSED', result)
        self.assertEqual(load_run_state(root, identifier)['source_head'], landed['commit'])

    @staticmethod
    def acceptance(identifier):
        return json.loads(json.dumps(routed_acceptance(identifier)).replace('orders', 'invoices'))

    @staticmethod
    def implement(project, context):
        target = project / 'modules/invoices/service.py'
        target.parent.mkdir(parents=True)
        target.write_text('VALUE = 1\n', encoding='utf-8')
        return {'summary': 'Implement invoice version', 'questions': []}

    def test_source_code_advance_after_start_rejects_approval(self):
        self.check_source_advance(lesson=False)

    def test_lesson_advance_after_start_rejects_approval(self):
        self.check_source_advance(lesson=True)

    def check_source_advance(self, *, lesson):
        root, identifier, services = self.case()
        paused = run_change(root, identifier, services)
        self.assertEqual(paused['status'], 'DESIGN_APPROVAL_REQUIRED')
        state = load_run_state(root, identifier)
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'source'
            git(root, 'worktree', 'add', '-q', str(source), state['source_branch'])
            try:
                if lesson:
                    write_lesson(source, sample(baseline_commit=state['source_head']))
                    git(source, 'add', 'openspec/lessons')
                    git(source, 'commit', '-qm', 'external lesson')
                else:
                    commit_file(source, 'app/service.py', 'def serve():\n    return "changed"\n')
                self.assertNotEqual(git(source, 'rev-parse', 'HEAD'), state['source_head'])
                with self.assertRaisesRegex(StoryLoopError, 'Saved source branch moved'):
                    approve_design(root, identifier, paused['plan_id'])
                resumed = run_change(root, identifier, services)
                self.assertEqual(resumed['status'], 'DESIGN_REVISION_REQUIRED')
                self.assertIn('Saved source branch moved', resumed['reason'])
                self.assertEqual(load_run_state(root, identifier)['source_head'], state['source_head'])
                self.assertEqual(load_run_state(root, identifier)['attempt'], 0)
            finally:
                git(root, 'worktree', 'remove', str(source))
