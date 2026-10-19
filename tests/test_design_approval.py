"""Explicit human design and split decisions on current run bindings."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from storyloop import design_approval as approvals
from storyloop.models import StoryLoopError
from storyloop.project_loop import LoopServices, load_run_state, run_change
from storyloop.project_loop_support import save_run
from tests import test_routing_state
from tests.test_routing_planning import answer
from tests.test_project_loop import smoke_payload
from tests.test_structured_output import passing_review


class BindingTests(unittest.TestCase):
    def test_complete_canonical_binding(self):
        binding = dict(product_digest='p', source_commit='s', scope_digest='x',
                       suite_digest='t', policy_digest='r', candidate_digest='a')
        self.assertTrue(approvals.approval_matches({'binding': binding}, binding))
        for key in binding:
            changed = dict(binding, **{key: 'different'})
            self.assertFalse(approvals.approval_matches({'binding': binding}, changed))
            missing = dict(binding)
            missing.pop(key)
            self.assertFalse(approvals.approval_matches({'binding': missing}, binding))
        self.assertFalse(approvals.approval_matches(None, binding))
        self.assertFalse(approvals.approval_matches({'binding': {'a': True}}, {'a': 1}))


class DesignApprovalTests(unittest.TestCase):
    setUp = test_routing_state.RunRoutingTests.setUp

    def start(self, response=None):
        self.calls = []
        def forbidden(*args):
            self.calls.append('called')
            raise AssertionError('Approval gate must precede execution')
        self.services = LoopServices(lambda p, c: smoke_payload(self.identifier),
                                    forbidden, passing_review, characterize=forbidden,
                                    design=lambda p, c: response or answer())
        result = run_change(self.root, self.identifier, self.services)
        self.assertEqual(result['status'], 'DESIGN_APPROVAL_REQUIRED')
        self.assertEqual(self.calls, [])
        self.state = load_run_state(self.root, self.identifier)
        self.plan = self.state['planning']['plan']

    def approve(self, option=None):
        return approvals.approve_design(self.root, self.identifier, self.plan['id'], option_id=option)

    def test_explicit_replan_invalidates_candidate_and_review_evidence(self):
        self.start()
        state = load_run_state(self.root, self.identifier)
        state['cost_candidate'] = {'digest': 'previous-candidate'}
        state['pending_review'] = {'digest': 'previous-candidate', 'review': {'findings': []}}
        state['review_disposition'] = {'attempt': state['attempt'], 'fix': [{'id': 'old'}]}
        save_run(self.root, state)
        with patch.object(approvals, '_validate_context', side_effect=StoryLoopError('binding rejected')):
            with self.assertRaisesRegex(StoryLoopError, 'binding rejected'):
                approvals.replan(self.root, self.identifier, 'Reconsider candidate and plan')
        self.assertEqual(load_run_state(self.root, self.identifier), state)
        replanned = approvals.replan(self.root, self.identifier, 'Reconsider candidate and plan')
        self.assertEqual(replanned['attempt'], state['attempt'])
        for key in ('cost_candidate', 'pending_review', 'review_disposition'):
            self.assertNotIn(key, replanned)
            self.assertNotIn(key, load_run_state(self.root, self.identifier))
        self.assertEqual(replanned['planning']['history'][-1]['plan'], self.plan)

    def test_run_and_resume_do_not_approve_or_spend_attempt(self):
        self.start()
        self.assertEqual(run_change(self.root, self.identifier, self.services)['status'],
                         'DESIGN_APPROVAL_REQUIRED')
        self.assertEqual(load_run_state(self.root, self.identifier)['attempt'], 0)
        self.assertNotIn('approval', self.plan)

    def test_approval_only_records_and_is_idempotent(self):
        self.start()
        first = self.approve()
        self.assertEqual(first, self.approve())
        self.assertEqual(first['actor'], 'user')
        self.assertEqual(first['option_id'], 'main')
        self.assertEqual(self.calls, [])

    def test_changed_selection_remeasures_and_clears_prior_checks(self):
        response = answer()
        option = deepcopy(response['options'][0])
        option.update(id='alternate', creates=['app/extra.py'])
        response['options'].append(option)
        self.start(response)
        self.approve()
        state = load_run_state(self.root, self.identifier)
        state['planning']['consistency'] = {'status': 'PASSED'}
        state['characterization'] = {'status': 'GREEN'}
        save_run(self.root, state)
        with patch('storyloop.storage.os.replace', side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):
                self.approve('alternate')
        unchanged = load_run_state(self.root, self.identifier)
        self.assertEqual(unchanged['planning']['plan']['approval']['option_id'], 'main')
        self.assertIn('consistency', unchanged['planning'])
        self.approve('alternate')
        state = load_run_state(self.root, self.identifier)
        self.assertEqual(state['routing']['scope']['creates'], ['app/extra.py'])
        self.assertEqual(state['routing']['budget_source'], 'tier')
        self.assertEqual(state['routing']['limit'], 4)
        self.assertNotIn('consistency', state['planning'])
        self.assertNotIn('characterization', state)

    def test_stale_bindings_rejected_even_when_plan_done(self):
        self.start()
        self.approve()
        for key in self.plan['binding']:
            state = deepcopy(self.state)
            state['planning']['plan']['binding'][key] = 'stale'
            save_run(self.root, state)
            with self.subTest(key=key), self.assertRaises(StoryLoopError):
                self.approve()
            result = run_change(self.root, self.identifier, self.services)
            self.assertEqual(result['status'], 'DESIGN_REVISION_REQUIRED')

    def test_atomic_failure_leaves_no_approval(self):
        self.start()
        with patch('storyloop.storage.os.replace', side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):
                self.approve()
        self.assertNotIn('approval', load_run_state(self.root, self.identifier)['planning']['plan'])

    def test_replan_only_planning_and_retains_budget_attempt(self):
        self.start()
        self.state['attempt'] = 2
        save_run(self.root, self.state)
        approvals.replan(self.root, self.identifier, 'Try a narrower design')
        state = load_run_state(self.root, self.identifier)
        self.assertEqual(state['attempt'], 2)
        self.assertNotIn('plan', state['planning'])
        self.assertEqual(state['routing']['limit'], 4)
        for status in ('NEEDS_SPEC_FIX', 'ORACLE_DEFECT', 'NEEDS_INPUT'):
            state.update(status=status)
            state.pop('phase', None)
            state.pop('stop', None)
            save_run(self.root, state)
            with self.assertRaises(StoryLoopError):
                approvals.replan(self.root, self.identifier, 'wrong entry')

    def test_split_waiver_does_not_approve_design(self):
        self.start()
        from storyloop.intake import digest
        state = load_run_state(self.root, self.identifier)
        state['routing']['policy']['split_files'] = 1
        state['routing']['policy']['heavy_existing'] = 1
        state['routing']['policy_digest'] = digest(state['routing']['policy'])
        from storyloop.planning import validate_plan
        response = answer()
        response['options'][0]['creates'] = ['app/extra.py']
        state['planning']['plan'] = validate_plan(response, self.root, self.change, state)
        save_run(self.root, state)
        result = run_change(self.root, self.identifier, self.services)
        self.assertEqual(result['status'], 'SPLIT_RECOMMENDED')
        state = load_run_state(self.root, self.identifier)
        assessment_id = state['planning']['assessment']['id']
        for reason in ('', ' '):
            with self.assertRaises(StoryLoopError):
                approvals.approve_continue_heavy(self.root, self.identifier, assessment_id, reason)
        with self.assertRaises(StoryLoopError):
            approvals.approve_continue_heavy(self.root, self.identifier, 'old', 'needed')
        approvals.approve_continue_heavy(self.root, self.identifier, assessment_id, 'Single deploy required')
        self.assertEqual(run_change(self.root, self.identifier, self.services)['status'],
                         'DESIGN_APPROVAL_REQUIRED')
        self.assertNotIn('approval', load_run_state(self.root, self.identifier)['planning']['plan'])

    def test_candidate_drift_rejects_approval(self):
        self.start()
        from storyloop.intake import digest
        from storyloop.smoke_runner import implementation_digest
        state = load_run_state(self.root, self.identifier)
        plan = state['planning']['plan']
        plan['binding']['candidate_digest'] = implementation_digest(self.root)
        plan['id'] = digest({k: v for k, v in plan.items() if k not in {'id', 'status', 'approval'}})
        self.plan = plan
        save_run(self.root, state)
        self.approve()
        (self.root / 'app/service.py').write_text('changed candidate\n')
        with self.assertRaises(StoryLoopError):
            self.approve()
        self.assertEqual(run_change(self.root, self.identifier, self.services)['status'],
                         'DESIGN_REVISION_REQUIRED')

    def test_project_lock_prevents_concurrent_approval(self):
        self.start()
        from storyloop.storage import locked
        with locked(self.root):
            with self.assertRaisesRegex(StoryLoopError, 'project lock'):
                self.approve()
        self.approve()

    def test_option_expansion_requires_new_split_and_design_decisions(self):
        response = answer()
        option = deepcopy(response['options'][0])
        option.update(id='large', creates=[f'app/new{i}.py' for i in range(15)])
        response['options'].append(option)
        self.start(response)
        self.approve()
        result = self.approve('large')
        self.assertEqual(result['status'], 'SPLIT_RECOMMENDED')
        state = load_run_state(self.root, self.identifier)
        self.assertNotIn('approval', state['planning']['plan'])
        self.assertEqual(state['planning']['pending_option']['option_id'], 'large')
        assessment_id = state['planning']['assessment']['id']
        approvals.approve_continue_heavy(self.root, self.identifier, assessment_id, 'One delivery')
        self.assertEqual(run_change(self.root, self.identifier, self.services)['status'],
                         'DESIGN_APPROVAL_REQUIRED')
        record = self.approve('large')
        self.assertEqual(record['option_id'], 'large')
        self.assertEqual(record['actor'], 'user')

    def test_stale_pending_option_rejected(self):
        self.start()
        self.state['planning']['pending_option'] = {'plan_id': 'stale', 'option_id': 'main'}
        save_run(self.root, self.state)
        with self.assertRaisesRegex(StoryLoopError, 'pending option'):
            self.approve()

    def test_answer_never_authorizes_design(self):
        self.start()
        from storyloop.answers import record_answers
        self.state.update(status='NEEDS_INPUT', phase='planning', questions=['Which approach?'])
        save_run(self.root, self.state)
        record_answers(self.root, self.identifier, ['Approved, proceed'])
        self.assertEqual(run_change(self.root, self.identifier, self.services)['status'],
                         'DESIGN_APPROVAL_REQUIRED')
        self.assertNotIn('approval', load_run_state(self.root, self.identifier)['planning']['plan'])

    def test_planning_input_replan_and_supersede_preserve_history(self):
        self.start()
        from storyloop.supersede import supersede_change
        from tests.test_intake import assessed_product
        for status in approvals.PLANNING_STOPS:
            self.state['status'] = status
            save_run(self.root, self.state)
            with self.assertRaises(StoryLoopError):
                supersede_change(self.root, self.identifier, 'revise', lambda c: assessed_product())
        self.state.update(status='NEEDS_INPUT', phase='planning')
        save_run(self.root, self.state)
        approvals.replan(self.root, self.identifier, 'clarify design')
        revised = supersede_change(self.root, self.identifier, 'revise',
                                   lambda c: assessed_product(), reason='scope changed')
        self.assertEqual(revised['routing_version'], 1)
        self.assertEqual(revised['status'], 'DRAFT')
        from tests.test_baseline_behavior import git
        self.assertEqual(git(self.root, 'branch', '--show-current'), self.state['feature_branch'])

    def test_external_source_branch_movement_rejects_approval(self):
        self.start()
        from tests.test_baseline_behavior import git
        git(self.root, 'update-ref', 'refs/heads/' + self.state['source_branch'],
            self.state['contract_commit'])
        with self.assertRaisesRegex(StoryLoopError, 'source branch'):
            self.approve()

    def test_reapproval_never_reuses_checks_from_stale_approval(self):
        self.start()
        self.approve()
        state = load_run_state(self.root, self.identifier)
        state['planning']['plan']['approval']['binding']['suite_digest'] = 'stale'
        state['planning']['consistency'] = {'status': 'PASSED'}
        save_run(self.root, state)
        self.approve()
        self.assertNotIn('consistency', load_run_state(self.root, self.identifier)['planning'])

    def failed_characterization(self):
        response = answer()
        alternate = deepcopy(response['options'][0])
        alternate.update(id='alternate', creates=['app/extra.py'])
        response['options'].append(alternate)
        self.start(response)
        self.approve()
        seen = []
        def characterize(project, context):
            seen.append(context['design']['id'])
            expected = 'wrong' if len(seen) == 1 else 'ok'
            (project / 'tests/test_lock.py').write_text(
                'import unittest\nfrom app.service import serve\n'
                'class Lock(unittest.TestCase):\n'
                '    def test_behavior(self):\n'
                f'        self.assertEqual(serve(), {expected!r})\n')
            return {'summary': 'Lock serve', 'touched': ['app/service.py'],
                    'tests': ['tests/test_lock.py'], 'questions': []}
        services = LoopServices(lambda p, c: smoke_payload(self.identifier),
                                lambda p, c: {'summary': 'Reached implement', 'questions': ['Stop here']},
                                passing_review, characterize=characterize, design=lambda p, c: response)
        self.assertEqual(run_change(self.root, self.identifier, services)['status'],
                         'CHARACTERIZATION_FAILED')
        return services, seen

    def assert_characterization_restarted(self, services, seen, option):
        state = load_run_state(self.root, self.identifier)
        self.assertEqual(state['status'], 'DESIGN_APPROVAL_REQUIRED')
        self.assertEqual(state['phase'], 'planning')
        self.assertEqual(state['attempt'], 0)
        self.assertEqual(state['routing']['limit'], 4)
        for key in ('pending_characterization', 'stop', 'unit', 'error', 'next'):
            self.assertNotIn(key, state)
        self.assertEqual(run_change(self.root, self.identifier, services)['status'], 'NEEDS_INPUT')
        state = load_run_state(self.root, self.identifier)
        self.assertEqual(seen, ['main', option])
        self.assertEqual(state['characterization']['status'], 'GREEN')
        self.assertEqual(set(state['characterization']['locks']), {'app/service.py'})
        self.assertEqual(state['attempt'], 1)

    def test_failed_characterization_change_option_restarts_gate(self):
        services, seen = self.failed_characterization()
        self.approve('alternate')
        self.assert_characterization_restarted(services, seen, 'alternate')

    def test_failed_characterization_stale_same_option_restarts_gate(self):
        services, seen = self.failed_characterization()
        state = load_run_state(self.root, self.identifier)
        state['planning']['plan']['approval']['binding']['suite_digest'] = 'stale'
        save_run(self.root, state)
        self.approve()
        self.assert_characterization_restarted(services, seen, 'main')

    def test_idempotent_approval_preserves_failed_characterization_resume(self):
        services, seen = self.failed_characterization()
        before = load_run_state(self.root, self.identifier)
        self.approve()
        self.assertEqual(load_run_state(self.root, self.identifier), before)
        path = self.root / 'tests/test_lock.py'
        path.write_text(path.read_text().replace("'wrong'", "'ok'"))
        self.assertEqual(run_change(self.root, self.identifier, services)['status'], 'NEEDS_INPUT')
        self.assertEqual(seen, ['main'])
        self.assertEqual(load_run_state(self.root, self.identifier)['characterization']['status'], 'GREEN')

    def test_design_decisions_cannot_replace_routed_defect_repair(self):
        response = answer()
        option = deepcopy(response['options'][0])
        option['id'] = 'alternate'
        response['options'].append(option)
        self.start(response)
        self.approve()
        baseline = load_run_state(self.root, self.identifier)
        for status in ('NEEDS_SPEC_FIX', 'ORACLE_DEFECT'):
            for stale in (False, True):
                state = deepcopy(baseline)
                state.update(status=status, routed={'source': 'review', 'target': 'contract'},
                             stop={'status': status, 'blocker': 'repair required'})
                if stale:
                    state['planning']['plan']['approval']['binding']['suite_digest'] = 'stale'
                actions = [lambda: self.approve('main' if stale else 'alternate'),
                           lambda: approvals.approve_continue_heavy(
                               self.root, self.identifier, 'assessment', 'continue'),
                           lambda: approvals.replan(self.root, self.identifier, 'replan')]
                for action in actions:
                    save_run(self.root, state)
                    with self.subTest(status=status, stale=stale, action=action):
                        with self.assertRaisesRegex(StoryLoopError, 'repair'):
                            action()
                        self.assertEqual(load_run_state(self.root, self.identifier), state)
                        with self.assertRaisesRegex(StoryLoopError, 'overrule'):
                            run_change(self.root, self.identifier, self.services)
        self.assertEqual(self.calls, [])

    def test_option_change_preserves_other_stop_state(self):
        response = answer()
        option = deepcopy(response['options'][0])
        option['id'] = 'alternate'
        response['options'].append(option)
        self.start(response)
        self.approve()
        state = load_run_state(self.root, self.identifier)
        state.update(status='BUDGET_EXHAUSTED', stop={'status': 'BUDGET_EXHAUSTED'},
                     attempt=4, error='budget stop')
        save_run(self.root, state)
        self.approve('alternate')
        actual = load_run_state(self.root, self.identifier)
        for key in ('status', 'stop', 'attempt', 'error'):
            self.assertEqual(actual.get(key), state[key])
