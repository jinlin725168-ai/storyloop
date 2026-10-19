import unittest
from storyloop.finding_disposition import dispose


class DispositionTests(unittest.TestCase):
    def test_ordered_table(self):
        rows = [({'introduced': False, 'target': 'oracle'}, 'legacy_issue'),
                ({'target': 'out_of_scope'}, 'out_of_scope'),
                ({}, 'fix'), ({'target': 'oracle'}, 'stop'),
                ({'target': 'contract'}, 'stop'), ({'confidence': 60}, 'focus'),
                ({'confidence': 20, 'severity': 'P0'}, 'human'),
                ({'confidence': 20, 'severity': 'P1'}, 'human')]
        for extra, bucket in rows:
            with self.subTest(bucket=bucket, extra=extra):
                result = dispose([{'message': 'bug', **extra}], {}, [])
                self.assertEqual(len(result[bucket]), 1)
        self.assertEqual(dispose([{'confidence': 10, 'severity': 'P2'}], {}, [])['dropped'], 1)

    def test_stable_ids_and_thresholds(self):
        findings = [{'message': 'bug', 'confidence': 75}]
        result = dispose(findings, {'fix': 70, 'focus': 40}, [])
        self.assertEqual(result, dispose(findings, {'fix': 70, 'focus': 40}, []))
        self.assertTrue(result['fix'][0]['id'])
        self.assertNotIn('id', findings[0])

from types import SimpleNamespace
from unittest.mock import patch
from storyloop.models import StoryLoopError
from storyloop.review_disposition import perform_review
from storyloop.review_context import verification_active


def review_answer():
    return {'verdict': 'pass', 'summary': 'bug', 'questions': [], 'coverage': ['code'],
            'findings': [{'message': 'bug', 'confidence': 90, 'introduced': True,
                          'severity': 'P1', 'trigger': 'x', 'impact': 'bad', 'fix': 'fix'}]}


class ReviewIntegrationTests(unittest.TestCase):
    def run_review(self, service, state=None, tier='standard', context=None):
        state = state if state is not None else {'attempt': 1, 'change_id': 'C'}
        config = {'roles': {'review': {'tier': tier, 'reason': 'test'}}}
        with patch('storyloop.review_disposition.implementation_digest', return_value='digest'), \
                patch('storyloop.review_disposition.save_run'), \
                patch('storyloop.review_disposition.read_only_call', side_effect=lambda p, c, f: f()):
            return perform_review(None, state, SimpleNamespace(review=service), context or {}, config)

    def test_standard_verify_unconfirmed_never_fails(self):
        calls = []
        def service(project, context):
            calls.append(context)
            if context.get('mode') == 'verify':
                self.assertTrue(verification_active())
                return {'results': [{'id': item['id'], 'confirmed': False, 'reason': 'no'}
                                    for item in context['findings']]}
            return review_answer()
        state = {'attempt': 1, 'change_id': 'C'}
        self.assertEqual(self.run_review(service, state)['verdict'], 'pass')
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(state['review_disposition']['focus']), 1)
        self.assertNotIn('pending_review', state)

    def test_strong_does_not_verify_and_ignores_agent_pass(self):
        calls = []
        def service(p, c):
            calls.append(c)
            return review_answer()
        self.assertEqual(self.run_review(service, tier='strong')['verdict'], 'fail')
        self.assertEqual(len(calls), 1)

    def test_verify_cost_resume_does_not_repeat_first_review(self):
        calls = []
        stopped = False
        def service(p, c):
            nonlocal stopped
            calls.append(c.get('mode', 'review'))
            if c.get('mode') != 'verify':
                return review_answer()
            if not stopped:
                stopped = True
                raise StoryLoopError('limit', code='COST_LIMIT_REACHED')
            return {'results': [{'id': c['findings'][0]['id'], 'confirmed': True, 'reason': 'yes'}]}
        state = {'attempt': 1, 'change_id': 'C'}
        with self.assertRaises(StoryLoopError):
            self.run_review(service, state)
        self.assertIn('review', state['pending_review'])
        self.assertEqual(self.run_review(service, state)['verdict'], 'fail')
        self.assertEqual(calls, ['review', 'verify', 'verify'])

    def test_checklist_repair_once_in_same_attempt(self):
        calls = []
        decision = '{"criterion_id":"A1","path":"x","rule":"r"}'
        def service(p, c):
            calls.append(c)
            answer = review_answer()
            answer['findings'] = []
            if 'validation_errors' in c:
                answer['checklist'] = [{'decision': decision, 'status': 'pass'}]
            return answer
        state = {'attempt': 1, 'change_id': 'C', 'planning': {
            'plan': {'status': 'DONE', 'approval': {'option_id': 'a'}}}}
        context = {'design_plan': {'constraints': [{'path': 'x', 'rule': 'r', 'criterion_id': 'A1'}]}}
        self.assertEqual(self.run_review(service, state, context=context)['verdict'], 'pass')
        self.assertEqual(len(calls), 2)
        self.assertEqual(state['attempt'], 1)

    def test_duplicate_and_unknown_checklist_rejected(self):
        from storyloop.finding_disposition import check_checklist
        for rows in ([{'decision': 'a'}, {'decision': 'a'}], [{'decision': 'b'}], []):
            with self.assertRaises(StoryLoopError):
                check_checklist({'checklist': rows}, ['a'])

    def test_profile_schema_requires_fields_and_bounds(self):
        from storyloop.role_schemas import schema_errors, schema_for
        schema = schema_for('review', {'role_profile': True})
        answer = review_answer()
        self.assertEqual(schema_errors(answer, schema), [])
        answer['findings'][0]['confidence'] = 101
        self.assertTrue(schema_errors(answer, schema))
        del answer['findings'][0]['confidence']
        self.assertTrue(schema_errors(answer, schema))


class InvocationTests(unittest.TestCase):
    def test_actual_command_verify_uses_strong_despite_standard_role_and_fake_telemetry(self):
        import json
        import tempfile
        from pathlib import Path
        from storyloop.project_agents import invoke_project_agent
        from storyloop.baseline import load_project_config
        from storyloop.storage import write_json
        from storyloop.telemetry import ledger_entries
        from tests.test_structured_output import command_project
        script = '''import json,sys
r=json.load(sys.stdin)
assert r['role_profile']['tier']=='strong'
print(json.dumps({'results':[{'id':'F1','confirmed':True,'reason':'yes'}],
                  '_telemetry':{'tier_requested':'standard'}}))
'''
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            command_project(root, script)
            config = load_project_config(root)
            config['roles'] = {'review': {'tier': 'standard', 'reason': 'test'}}
            write_json(root / '.storyloop/config.json', config)
            with patch('storyloop.adapter_capabilities.ensure_checked'), \
                    patch('storyloop.adapter_capabilities.require_role_target') as capability:
                from storyloop.review_context import strong_verification
                with strong_verification():
                    result = invoke_project_agent(root, 'review', {'change_id': 'FEAT-1', 'mode': 'verify'})
            self.assertTrue(result['results'][0]['confirmed'])
            self.assertEqual(capability.call_args.args[-1]['tier'], 'strong')
            self.assertEqual(ledger_entries(root, 'FEAT-1')[-1]['tier_requested'], 'strong')

    def test_verify_cost_stop_run_resume_keeps_candidate(self):
        from storyloop.baseline import load_project_config
        from storyloop.storage import write_json
        from storyloop.project_loop import LoopServices, run_change, load_run_state
        from storyloop.cost_limits import check_before_call
        from tests.test_structured_output import approved_change
        from tests.test_project_loop import smoke_payload
        from tests.test_agent_unavailable import implement_after, single_attempt
        temporary, root, change_id = approved_change()
        implementations, reviews = [], []
        def service(project, context):
            reviews.append(context.get('mode', 'review'))
            if context.get('mode') == 'verify':
                check_before_call(project, change_id, load_project_config(project))
                return {'results': [{'id': item['id'], 'confirmed': False, 'reason': 'no'}
                                    for item in context['findings']]}
            return review_answer()
        services = LoopServices(lambda p, c: smoke_payload(change_id),
                                implement_after(0, implementations), service)
        try:
            single_attempt(root)
            config = load_project_config(root)
            config.update(roles={'review': {'tier': 'standard', 'reason': 'test'}}, limits={'max_calls': 0})
            write_json(root / '.storyloop/config.json', config)
            with patch('storyloop.cross_vendor.prepare_run'):
                self.assertEqual(run_change(root, change_id, services)['status'], 'COST_LIMIT_REACHED')
                self.assertIn('review', load_run_state(root, change_id)['pending_review'])
                config['limits']['max_calls'] = 1
                write_json(root / '.storyloop/config.json', config)
                self.assertEqual(run_change(root, change_id, services)['status'], 'PASSED')
            self.assertEqual(implementations, [1])
            self.assertEqual(reviews, ['review', 'verify', 'verify'])
            self.assertNotIn('pending_review', load_run_state(root, change_id))
        finally:
            temporary.cleanup()


class EdgeTests(unittest.TestCase):
    def test_introduced_false_never_verifies(self):
        harness = ReviewIntegrationTests()
        answer = review_answer()
        answer['findings'][0]['introduced'] = False
        calls = []
        result = harness.run_review(lambda p, c: calls.append(c) or answer)
        self.assertEqual(result['verdict'], 'pass')
        self.assertEqual(len(calls), 1)

    def test_verify_override_resets_after_exception(self):
        from storyloop.review_context import strong_verification
        with self.assertRaises(RuntimeError):
            with strong_verification():
                self.assertTrue(verification_active())
                raise RuntimeError('failure')
        self.assertFalse(verification_active())

    def test_pending_candidate_mismatch_rejects_without_call(self):
        state = {'change_id': 'C', 'attempt': 2, 'pending_review': {'attempt': 1, 'digest': 'digest'}}
        with self.assertRaisesRegex(StoryLoopError, 'current candidate'):
            ReviewIntegrationTests().run_review(lambda p, c: self.fail('unexpected call'), state)

    def test_no_profile_new_findings_record_unverified_reason(self):
        state = {'change_id': 'C', 'attempt': 1}
        with patch('storyloop.review_disposition.save_run'), \
                patch('storyloop.review_disposition.read_only_call', side_effect=lambda p, c, f: f()):
            result = perform_review(None, state, SimpleNamespace(review=lambda p, c: review_answer()), {}, {})
        self.assertEqual(result['verdict'], 'fail')
        self.assertEqual(state['review_disposition']['verification']['reason'], 'PROFILE_NOT_CONFIGURED')

    def test_exhausted_checklist_repair_refunds_quality_attempt(self):
        from storyloop.project_loop import _attempts
        state = {'change_id': 'C', 'attempt': 0, 'pending_review': {}, 'cost_candidate': {'response': {}}}
        error = StoryLoopError('bad checklist', code='AGENT_CONTRACT_INVALID',
                               details={'review_semantic': True})
        with patch('storyloop.project_loop._run_attempt', side_effect=error), \
                patch('storyloop.project_loop.save_run'), \
                patch('storyloop.stop_report.build_stop', return_value={}), \
                patch('storyloop.project_loop.datetime'):
            with self.assertRaises(StoryLoopError):
                _attempts(None, {}, state, None, {'max_attempts': 1}, 0)
        self.assertEqual(state['attempt'], 0)
        self.assertIn('cost_candidate', state)
        self.assertNotIn('pending_review', state)
        self.assertNotIn('failures', state)

    def test_questions_with_only_focus_findings_use_engine_pass(self):
        answer = review_answer()
        answer.pop('coverage')
        answer['questions'] = ['uncertainty about finding']
        answer['findings'][0]['confidence'] = 60
        state = {'change_id': 'C', 'attempt': 1}
        with patch('storyloop.review_disposition.save_run'), \
                patch('storyloop.review_disposition.read_only_call', side_effect=lambda p, c, f: f()):
            result = perform_review(None, state, SimpleNamespace(review=lambda p, c: answer), {}, {})
        self.assertEqual(result['verdict'], 'pass')
        self.assertFalse(result['findings'])


class ReviewFixRoundTests(unittest.TestCase):
    def test_duplicate_agent_ids_bind_independent_verification(self):
        def service(p, context):
            if context.get('mode') == 'verify':
                findings = context['findings']
                self.assertNotEqual(findings[0]['id'], findings[1]['id'])
                return {'results': [{'id': row['id'], 'confirmed': index == 1, 'reason': 'checked'}
                                    for index, row in enumerate(findings)]}
            answer = review_answer()
            answer['findings'] = [{**answer['findings'][0], 'id': 'duplicate', 'message': message}
                                  for message in ('first', 'second')]
            return answer
        state = {'change_id': 'C', 'attempt': 1}
        result = ReviewIntegrationTests().run_review(service, state)
        self.assertEqual(result['verdict'], 'fail')
        self.assertEqual([row['message'] for row in result['findings']], ['second'])
        self.assertEqual([row['message'] for row in state['review_disposition']['focus']], ['first'])

    def test_repair_contract_error_preserves_candidate_and_missing_decisions_on_run_resume(self):
        from storyloop.baseline import load_project_config
        from storyloop.storage import write_json
        from storyloop.project_loop import LoopServices, run_change, load_run_state
        from tests.test_structured_output import approved_change
        from tests.test_project_loop import smoke_payload
        from tests.test_agent_unavailable import implement_after, single_attempt
        temporary, root, change_id = approved_change()
        implementations, reviews = [], []
        def service(project, context):
            reviews.append(context)
            if 'validation_errors' in context:
                raise StoryLoopError('repair schema invalid', code='AGENT_CONTRACT_INVALID')
            answer = review_answer()
            answer['findings'] = []
            if len(reviews) > 2:
                answer['checklist'] = [{'decision': 'decision', 'status': 'pass'}]
            return answer
        services = LoopServices(lambda p, c: smoke_payload(change_id),
                                implement_after(0, implementations), service)
        try:
            single_attempt(root)
            config = load_project_config(root)
            config['roles'] = {'review': {'tier': 'strong'}}
            write_json(root / '.storyloop/config.json', config)
            with patch('storyloop.cross_vendor.prepare_run'), \
                    patch('storyloop.review_disposition._decisions', return_value=['decision']):
                with self.assertRaises(StoryLoopError) as caught:
                    run_change(root, change_id, services)
                self.assertEqual(caught.exception.code, 'AGENT_CONTRACT_INVALID')
                state = load_run_state(root, change_id)
                self.assertEqual(state['attempt'], 0)
                self.assertIn('cost_candidate', state)
                self.assertEqual(state['review_disposition']['checklist_missing'], ['decision'])
                self.assertEqual(caught.exception.details['checklist_missing'], ['decision'])
                self.assertEqual(run_change(root, change_id, services)['status'], 'PASSED')
            self.assertEqual(implementations, [1])
            self.assertEqual(load_run_state(root, change_id)['review_disposition']['checklist_missing'], [])
        finally:
            temporary.cleanup()

    def test_duplicate_findings_cannot_be_cleared_by_one_false_result(self):
        def service(p, context):
            if context.get('mode') == 'verify':
                return {'results': [{'id': context['findings'][0]['id'],
                                     'confirmed': False, 'reason': 'no'}]}
            answer = review_answer()
            answer['findings'] = [{**answer['findings'][0], 'id': 'same'} for _ in range(2)]
            return answer
        with self.assertRaisesRegex(StoryLoopError, 'each finding exactly once'):
            ReviewIntegrationTests().run_review(service)

    def test_repair_infrastructure_errors_keep_codes_and_missing_evidence(self):
        for code in ('COST_LIMIT_REACHED', 'AGENT_UNAVAILABLE'):
            with self.subTest(code=code):
                state = {'change_id': 'C', 'attempt': 1}
                failure = StoryLoopError('infrastructure', code=code)
                def service(p, context):
                    if 'validation_errors' in context:
                        raise failure
                    return review_answer()
                with patch('storyloop.review_disposition._decisions', return_value=['decision']):
                    with self.assertRaises(StoryLoopError) as caught:
                        ReviewIntegrationTests().run_review(service, state)
                self.assertEqual(caught.exception.code, code)
                self.assertNotIn('review_semantic', caught.exception.details)
                self.assertEqual(state['pending_review']['checklist_missing'], ['decision'])
                self.assertEqual(state['review_disposition']['checklist_missing'], ['decision'])
