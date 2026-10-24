"""Commit-bound L2 planning, native protocol, and execution floor regressions."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from storyloop.baseline import load_project_config
from storyloop.intake import _validate_locations, digest
from storyloop.models import StoryLoopError
from storyloop.planning import ensure_routing_plan, required_locks, validate_plan
from storyloop.routing_policy import DEFAULT_POLICY
from storyloop.routing_state import open_routing, run_config
from storyloop.storage import write_json
from tests.routing_support import readiness_repository
from tests.test_baseline_behavior import git
from tests.test_intake import assessed_product


def answer(product=None):
    product = product or assessed_product()
    return {'main_option': 'main', 'options': [{
        'id': 'main', 'summary': 'Implement the approved contract',
        **deepcopy(product['assessment']['scope']),
        'risks': deepcopy(product['assessment']['risks']), 'forbidden_paths': [],
        'constraints': [{'criterion_id': 'AC-1', 'path': 'app/service.py',
                         'rule': 'Preserve existing callers'}],
        'regression': ['Run the existing unit and acceptance suites'],
        'migration': {'steps': [], 'reason': 'No persisted data changes'},
        'dependencies': [], 'principle_checks': []}],
        'questions': [], 'design': 'Implement AC-1 in the existing module.'}


class PlanningTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        readiness_repository(self.root, module_roots=['app'])
        self.commit = git(self.root, 'rev-parse', 'HEAD').strip()
        self.change = {'change_id': 'FEAT-planning', 'routing_version': 1,
                       'product': assessed_product(), 'approval': {'tier': 'light'},
                       'intake': {'decision': {'reasons': []}}}
        self.state = {'change_id': 'FEAT-planning', 'attempt': 0, 'status': 'CONTRACT_COMMITTED',
                      'contract_commit': self.commit, 'routing': open_routing(
                          self.change, DEFAULT_POLICY, {})}
        suite = patch('storyloop.planning_validation.smoke_digests', return_value={'suite.py': 'fixture'})
        suite.start()
        self.addCleanup(suite.stop)
        self.config = run_config(load_project_config(self.root), self.change, self.state)

    def plan(self, response=None):
        return validate_plan(response or answer(), self.root, self.change, self.state)

    def ensure(self, design=None):
        return ensure_routing_plan(self.root, self.change, self.state,
                                   SimpleNamespace(design=design), self.config)

    def test_lock_policy_preserves_refactor(self):
        for tier, nature, expected in [('light', 'fix', []), ('standard', 'feature', []),
                                      ('standard', 'refactor', ['app/a.py']),
                                      ('heavy', 'feature', ['app/a.py'])]:
            self.assertEqual(required_locks(tier, nature, ['app/a.py']), expected)

    def test_light_plan_is_complete_and_does_not_call_design(self):
        self.assertIsNone(self.ensure(lambda p, c: self.fail('light called design')))
        plan = self.state['planning']['plan']
        self.assertEqual(plan['status'], 'DONE')
        self.assertEqual(plan['binding']['source_commit'], self.commit)
        self.assertTrue(plan['options'][0]['regression'])
        self.assertTrue(plan['options'][0]['symbols'])
        self.assertEqual(self.state['routing']['effective_tier'], 'light')

    def test_standard_requires_service_and_does_not_skip(self):
        self.state['routing']['initial_tier'] = 'standard'
        self.state['routing']['effective_tier'] = 'standard'
        result = self.ensure()
        self.assertEqual(result['status'], 'NEEDS_INPUT')
        self.assertEqual(self.state['phase'], 'planning')
        self.assertNotIn('plan', self.state.get('planning', {}))

    def test_unknown_l1_completes_references_but_cannot_downgrade(self):
        self.change['product']['assessment']['scope']['symbols'] = []
        self.state['routing'].update(initial_tier='standard', effective_tier='standard')
        self.assertIsNone(self.ensure(lambda p, c: answer()))
        self.assertEqual(self.state['routing']['effective_tier'], 'standard')
        self.assertEqual(self.state['routing']['scope']['symbols'], answer()['options'][0]['symbols'])
        self.assertEqual(self.state['planning']['assessment']['decision']['unresolved'], [])

    def test_incomplete_l2_stops_and_cannot_implement(self):
        self.state['routing'].update(initial_tier='standard', effective_tier='standard')
        response = answer()
        response['options'][0]['symbols'] = []
        self.assertEqual(self.ensure(lambda p, c: response)['status'], 'NEEDS_INPUT')

    def test_plan_fields_scope_and_constraints_are_validated(self):
        mutations = [lambda a: a['options'][0].pop('symbols'),
                     lambda a: a['options'].append(deepcopy(a['options'][0])),
                     lambda a: a.update(main_option='absent'),
                     lambda a: a['options'][0].update(creates=['app/service.py']),
                     lambda a: a['options'][0].update(creates=['app/service.py/child.py']),
                     lambda a: a['options'][0].update(forbidden_paths=['app']),
                     lambda a: a['options'][0].update(regression=[]),
                     lambda a: a['options'][0]['constraints'][0].update(criterion_id='AC-missing'),
                     lambda a: a['options'][0]['migration'].update(reason='')]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                response = answer()
                mutate(response)
                with self.assertRaises(StoryLoopError):
                    self.plan(response)

    def test_terminal_recursive_forbidden_path_normalizes_before_scope_overlap(self):
        response = answer()
        response['options'][0]['creates'] = ['app/generated/new.py']
        response['options'][0]['forbidden_paths'] = ['app/generated/**']

        with self.assertRaisesRegex(StoryLoopError, 'Forbidden path overlaps plan scope'):
            self.plan(response)

    def test_terminal_recursive_forbidden_path_is_stored_as_literal_root(self):
        response = answer()
        response['options'][0]['forbidden_paths'] = ['tests/**']

        plan = self.plan(response)

        self.assertEqual(plan['options'][0]['forbidden_paths'], ['tests'])

    def test_unsupported_forbidden_glob_gets_one_semantic_repair(self):
        self.state['routing'].update(initial_tier='heavy', effective_tier='heavy')
        requests = []

        def design(project, request):
            requests.append(request)
            response = answer()
            response['options'][0]['forbidden_paths'] = ['tests/*'] if len(requests) == 1 else []
            return response

        self.assertIsNone(self.ensure(design))

        self.assertEqual(len(requests), 2)
        self.assertIn('Unsupported forbidden path wildcard', requests[1]['validation_errors'][0])

    def test_repeated_unsupported_forbidden_glob_fails_closed(self):
        self.state['routing'].update(initial_tier='heavy', effective_tier='heavy')
        requests = []

        def design(project, request):
            requests.append(request)
            response = answer()
            response['options'][0]['forbidden_paths'] = ['tests/[ab]']
            return response

        with self.assertRaisesRegex(StoryLoopError, 'Unsupported forbidden path wildcard'):
            self.ensure(design)

        self.assertEqual(len(requests), 2)

    def test_terminal_recursive_forbidden_path_rejects_empty_repository_root(self):
        for forbidden in ('/**', './**'):
            with self.subTest(forbidden=forbidden):
                response = answer()
                response['options'][0]['forbidden_paths'] = [forbidden]
                with self.assertRaises(StoryLoopError):
                    self.plan(response)

    def test_unknown_high_risk_becomes_question_not_safe_default(self):
        response = answer()
        response['options'][0]['risks']['migration']['value'] = 'unknown'
        self.state['routing'].update(initial_tier='standard', effective_tier='standard')
        self.assertEqual(self.ensure(lambda p, c: response)['status'], 'NEEDS_INPUT')

    def test_explicit_budget_and_default_budget_escalate_differently(self):
        for source, expected in [('tier', 4), ('explicit', 2)]:
            with self.subTest(source=source):
                self.state.pop('planning', None)
                self.state['routing'].update(initial_tier='light', effective_tier='light',
                                             budget_source=source, limit=2)
                self.config['max_attempts'] = 2
                self.change['product']['assessment']['risks']['sensitive']['value'] = 'yes'
                self.ensure(lambda p, c: answer())
                self.assertEqual(self.state['routing']['effective_tier'], 'heavy')
                self.assertEqual(self.config['max_attempts'], expected)
                self.assertEqual(self.state['routing']['budget_source'], source)

    def test_design_mode_and_one_semantic_repair(self):
        self.state['routing'].update(initial_tier='heavy', effective_tier='heavy')
        calls = []
        def design(project, context):
            calls.append(context)
            response = answer()
            if len(calls) == 1:
                response['options'][0]['regression'] = []
            return response
        self.ensure(design)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]['mode'], 'routing-plan')
        self.assertIn('validation_errors', calls[1])
        self.assertEqual(self.state['routing']['effective_tier'], 'heavy')

    def test_post_contract_evidence_path_is_repaired_against_explicit_source_commit(self):
        self.state['routing'].update(initial_tier='heavy', effective_tier='heavy')
        test = self.root / 'tests/test_service_characterization.py'
        test.parent.mkdir(exist_ok=True)
        test.write_text('CHARACTERIZED = True\n', encoding='utf-8')
        git(self.root, 'add', str(test.relative_to(self.root)))
        git(self.root, 'commit', '-q', '-m', 'characterize service')
        requests = []

        def design(project, request):
            requests.append(request)
            response = answer()
            if len(requests) == 1:
                response['options'][0]['risks']['sensitive']['evidence']['locations'] = [
                    'tests/test_service_characterization.py:1'
                ]
            return response

        self.assertIsNone(self.ensure(design))

        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0]['evidence_source_commit'], self.commit)
        self.assertIn('tests/test_service_characterization.py',
                      requests[1]['validation_errors'][0])
        self.assertIn(self.commit, requests[1]['validation_errors'][0])
        self.assertEqual(self.state['planning']['plan']['binding']['source_commit'], self.commit)

    def test_invalid_evidence_source_commit_remains_git_blocked_without_repair(self):
        with self.assertRaises(StoryLoopError) as raised:
            _validate_locations(self.root, '0' * 40, {
                'risks': answer()['options'][0]['risks'], 'criteria': [],
            })

        self.assertEqual(raised.exception.code, 'GIT_BLOCKED')

    def test_evidence_source_accepts_exact_unicode_file_path(self):
        evidence = self.root / 'app/证据.py'
        evidence.write_text('VALUE = 1\n', encoding='utf-8')
        git(self.root, 'add', str(evidence.relative_to(self.root)))
        git(self.root, 'commit', '-q', '-m', 'add unicode evidence')
        commit = git(self.root, 'rev-parse', 'HEAD').strip()

        _validate_locations(self.root, commit, {
            'risks': {'sensitive': {'evidence': {
                'locations': ['app/证据.py:1'], 'search_scope': ['app'],
            }}},
            'criteria': [],
        })

    def test_native_l3_request_explains_every_option_scope_and_names_repair_gap(self):
        from storyloop.project_agents import invoke_project_agent
        from tests.test_codex_adapter import FakeCodex
        config = load_project_config(self.root)
        config['adapter'] = 'codex'
        config.pop('agent_command', None)
        write_json(self.root / '.storyloop/config.json', config)
        self.state['routing'].update(initial_tier='heavy', effective_tier='heavy')
        self.state['planning'] = {
            'origin': 'L3', 'phase': 'REPLAN',
            'actual': {'touched': ['app/service.py'], 'creates': []},
        }
        incomplete = answer()
        incomplete['options'][0].update(touched=[], symbols=[], constraints=[])
        fake = FakeCodex([json.dumps(incomplete), json.dumps(answer())])

        with patch('storyloop.agent_transport.run_process', fake), \
                patch('storyloop.agent_transport._codex_executable', return_value=['codex']):
            self.ensure(lambda project, request: invoke_project_agent(project, 'design', request))

        initial_prompt = fake.calls[0][1]
        initial_request = json.loads(initial_prompt.split('\nRequest:\n', 1)[1])
        self.assertEqual(initial_request['payload']['evidence_source_commit'], self.commit)
        self.assertEqual(initial_request['payload']['replanning']['actual']['touched'],
                         ['app/service.py'])
        self.assertIn('every returned option', initial_prompt.lower())
        self.assertIn('payload.replanning.actual.touched', initial_prompt)
        self.assertIn('immutable source for every risk evidence location', initial_prompt)
        self.assertIn('only terminal /** is accepted', initial_prompt)
        repair_request = json.loads(fake.calls[1][1].split('\nRequest:\n', 1)[1])
        self.assertEqual(repair_request['payload']['validation_errors'], [
            "L3 option 'main' omits observed candidate scope; every option must include "
            'all observed paths; missing touched: app/service.py',
        ])

    def test_l3_rejects_an_incomplete_main_even_with_a_complete_alternative(self):
        self.state['planning'] = {
            'origin': 'L3',
            'actual': {'touched': ['app/service.py'], 'creates': []},
        }
        response = answer()
        complete = deepcopy(response['options'][0])
        complete['id'] = 'complete-alternative'
        response['options'][0].update(touched=[], symbols=[], constraints=[])
        response['options'].append(complete)

        with self.assertRaisesRegex(
                StoryLoopError,
                "L3 option 'main'.*missing touched: app/service.py"):
            self.plan(response)

    def test_readonly_design_cannot_save_plan_or_facts(self):
        self.state['routing'].update(initial_tier='standard', effective_tier='standard')
        def design(project, context):
            (project / 'app/service.py').write_text('polluted')
            return answer()
        with self.assertRaises(StoryLoopError) as caught:
            self.ensure(design)
        self.assertEqual(caught.exception.code, 'WORKSPACE_TAINTED')
        self.assertNotIn('plan', self.state.get('planning', {}))
        self.assertNotIn('facts', self.state)

    def test_light_missing_symbols_escalates_and_calls_design(self):
        self.change['product']['assessment']['scope']['symbols'] = []
        calls = []
        def design(project, context):
            calls.append(context)
            return answer()
        self.ensure(design)
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.state['routing']['effective_tier'], 'standard')
        self.assertEqual(self.config['max_attempts'], 3)

    def test_review_presentation_keeps_full_selected_plan(self):
        from storyloop.planning import review_focus, plan_projection
        self.ensure()
        self.assertEqual(len(review_focus(self.state)['subagents']['perspectives']), 0)
        projection = plan_projection(self.state)
        self.assertTrue(projection['constraints'])
        self.assertIn('migration', projection)
        self.assertEqual(projection['symbols'], answer()['options'][0]['symbols'])
        self.state['routing']['effective_tier'] = 'heavy'
        self.assertEqual(len(review_focus(self.state)['subagents']['perspectives']), 5)
        self.assertIn('oracle-strength', review_focus(self.state)['subagents']['perspectives'])

    def test_heavy_cannot_skip_required_characterization(self):
        from storyloop.project_loop_gates import ensure_characterization
        self.state['routing'].update(initial_tier='heavy', effective_tier='heavy')
        self.ensure(lambda p, c: answer())
        with self.assertRaisesRegex(StoryLoopError, 'characterization gate'):
            ensure_characterization(self.root, self.change, self.state,
                                    SimpleNamespace(characterize=None), self.config)

    def test_plan_history_reentry_is_idempotent_and_budget_not_consumed(self):
        self.ensure()
        before = deepcopy(self.state['routing']['history'])
        self.ensure(lambda p, c: self.fail('repeated design'))
        self.assertEqual(self.state['routing']['history'], before)
        self.assertEqual(self.state['attempt'], 0)

    def test_native_schema_switches_only_new_mode(self):
        from storyloop.role_schemas import schema_for, schema_errors
        self.assertEqual(schema_errors(answer(), schema_for('design', {'mode': 'routing-plan'})), [])
        self.assertIn('summary', schema_for('design', {})['properties'])

    def test_rename_scope_binds_old_and_new_paths_and_symbols(self):
        response = answer()
        response['options'][0]['creates'] = ['app/renamed.py']
        plan = self.plan(response)
        self.assertEqual(plan['options'][0]['touched'], ['app/service.py'])
        self.assertEqual(plan['options'][0]['creates'], ['app/renamed.py'])
        self.assertNotEqual(plan['binding']['scope_digest'], self.plan()['binding']['scope_digest'])
