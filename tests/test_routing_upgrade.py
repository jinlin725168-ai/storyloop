"""L3 monotonic routing and typed evidence integration."""
from copy import deepcopy
import unittest

from storyloop.routing_state import open_routing
from storyloop.routing_policy import DEFAULT_POLICY, evaluate
from tests.routing_support import signals


class UpgradeTests(unittest.TestCase):
    def test_scope_growth_is_not_only_a_tier_change(self):
        from storyloop.routing_upgrade import scope_expanded
        planned = {'touched': ['app/a.py'], 'creates': [], 'interfaces': []}
        actual = {**planned, 'creates': ['app/b.py']}
        self.assertTrue(scope_expanded(planned, actual))
        self.assertFalse(scope_expanded(planned, planned))
        self.assertTrue(scope_expanded(planned, {**planned, 'unknown_fields': ['interfaces']}))

    def test_budget_provenance_monotonic_and_guard_reentry(self):
        from storyloop.routing_state import advance_tier
        change = {'approval': {'tier': 'light'}, 'intake': {'decision': {'reasons': []}},
                  'product': {'assessment': {'scope': {}}}}
        for source, limit in [('tier', 4), ('explicit', 2)]:
            state = {'attempt': 2, 'routing': open_routing(change, DEFAULT_POLICY, {})}
            state['routing']['budget_source'] = source
            config = {'max_attempts': 2}
            facts = signals()
            facts['risks']['guard'] = 'yes'
            guard = evaluate(facts, DEFAULT_POLICY)
            advance_tier(state, guard, 'L3', 'guard', config)
            advance_tier(state, guard, 'L3', 'guard', config)
            self.assertEqual(state['routing']['effective_tier'], 'standard')
            self.assertEqual(len(state['routing']['history']), 1)
            facts['risks']['migration'] = 'yes'
            advance_tier(state, evaluate(facts, DEFAULT_POLICY), 'L3', 'migration', config)
            self.assertEqual(state['routing']['effective_tier'], 'heavy')
            self.assertEqual(config['max_attempts'], limit)
            self.assertEqual(state['routing']['budget_source'], source)
            self.assertEqual(state['attempt'], 2)

    def test_invalidation_preserves_locks_budget_and_evidence(self):
        from storyloop.routing_upgrade import invalidate_planning
        state = {'attempt': 2, 'routing': {'effective_tier': 'heavy', 'limit': 4},
                 'failures': [{'signature': 'prior'}],
                 'characterization': {'status': 'GREEN', 'locks': {'app/a.py': {'test.py': 'hash'}}},
                 'planning': {'plan': {'id': 'old', 'approval': {}}, 'consistency': {'status': 'DONE'}}}
        previous = deepcopy(state)
        invalidate_planning(state, 'scope grew')
        for key in ('attempt', 'routing', 'failures', 'characterization'):
            self.assertEqual(state[key], previous[key])
        self.assertNotIn('plan', state['planning'])
        self.assertNotIn('consistency', state['planning'])
        self.assertEqual(state['planning']['history'][0]['plan']['id'], 'old')


class L3IntegrationTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        from tests.routing_support import readiness_repository
        from storyloop.readiness import inspect_readiness, approve_readiness
        from storyloop.changes import create_change
        from storyloop.approvals import approve_change
        from tests.test_intake import assessed_product
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        readiness_repository(self.root, module_roots=['app', 'modules'], test_roots=['tests'])
        ready = inspect_readiness(self.root)
        approve_readiness(self.root, ready['digest'], 'verified local tests')
        product = assessed_product()
        product['assessment']['scope']['creates'] = ['modules/orders/service.py']
        change = create_change(self.root, 'orders', product)
        self.change = approve_change(self.root, change['change_id'], tier='standard')
        self.identifier = change['change_id']

    def services(self, role='implement', forbidden=False):
        from storyloop.project_loop import LoopServices
        from tests.test_project_loop import smoke_payload
        from tests.test_routing_planning import answer
        from tests.test_structured_output import passing_review
        def design(project, context):
            result = answer(self.change['product'])
            if forbidden:
                result['options'][0]['forbidden_paths'] = ['app/blocked.py']
            return result
        def implement(project, context):
            path = project / 'modules/orders/service.py'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('VALUE = 1\n')
            response = {'summary': 'implemented migration', 'questions': []}
            if forbidden:
                (project / 'app/blocked.py').write_text('blocked = 1\n')
            if role == 'implement':
                response['routing_risks'] = [{'risk': 'migration', 'statement': 'Storage format changes',
                                             'location': 'app/service.py:1'}]
            return response
        def review(project, context):
            result = passing_review(project, context)
            if context.get('mode') != 'consistency' and role == 'review':
                result['routing_risks'] = [{'risk': 'external_contract', 'statement': 'Public callers change',
                                           'location': 'app/service.py:1'}]
            return result
        return LoopServices(lambda p, c: smoke_payload(self.identifier), implement, review, design=design)

    def test_real_implement_risk_escalates_once_without_failure_pollution(self):
        from storyloop.project_loop import run_change, load_run_state
        services = self.services()
        result = run_change(self.root, self.identifier, services)
        self.assertEqual(result['status'], 'DESIGN_APPROVAL_REQUIRED')
        first = load_run_state(self.root, self.identifier)
        self.assertEqual(first['attempt'], 1)
        self.assertEqual(first['routing']['effective_tier'], 'heavy')
        self.assertEqual(first['failures'], [])
        self.assertEqual(first['planning']['origin'], 'L3')
        self.assertEqual(first['planning']['consumed_attempt'], 1)
        self.assertIn('candidate_digest', first['planning']['plan']['binding'])
        self.assertEqual(run_change(self.root, self.identifier, services)['status'], 'DESIGN_APPROVAL_REQUIRED')
        resumed = load_run_state(self.root, self.identifier)
        self.assertEqual(resumed['attempt'], 1)
        self.assertEqual(first['routing']['history'], resumed['routing']['history'])

    def test_review_discovered_risk_cannot_finish_green(self):
        from storyloop.project_loop import run_change, load_run_state
        result = run_change(self.root, self.identifier, self.services(role='review'))
        self.assertEqual(result['status'], 'DESIGN_APPROVAL_REQUIRED')
        self.assertEqual(load_run_state(self.root, self.identifier)['attempt'], 1)

    def test_forbidden_candidate_fails_before_risk_upgrade(self):
        from storyloop.project_loop import run_change, load_run_state
        result = run_change(self.root, self.identifier, self.services(forbidden=True))
        self.assertEqual(result['status'], 'BOUNDARY_VIOLATION')
        state = load_run_state(self.root, self.identifier)
        self.assertFalse(any(h['stage'] == 'L3' for h in state['routing']['history']))

    def test_approval_locks_reapproval_then_next_attempt_completes(self):
        from dataclasses import replace
        from storyloop.design_approval import approve_design
        from storyloop.project_loop import run_change, load_run_state
        calls = []
        def characterize(project, context):
            calls.append(context)
            (project / 'tests/test_lock.py').write_text(
                'import unittest\nfrom app.service import serve\n'
                'class Lock(unittest.TestCase):\n'
                '    def test_serve(self): self.assertEqual(serve(), "ok")\n')
            return {'summary': 'lock', 'touched': ['app/service.py'],
                    'tests': ['tests/test_lock.py'], 'questions': []}
        services = self.services()
        first = run_change(self.root, self.identifier, services)
        services = replace(services, characterize=characterize)
        approve_design(self.root, self.identifier, first['plan_id'])
        second = run_change(self.root, self.identifier, services)
        self.assertEqual(second['status'], 'DESIGN_APPROVAL_REQUIRED')
        self.assertNotEqual(first['plan_id'], second['plan_id'])
        self.assertEqual(load_run_state(self.root, self.identifier)['attempt'], 1)
        approve_design(self.root, self.identifier, second['plan_id'])
        self.assertEqual(run_change(self.root, self.identifier, services)['status'], 'PASSED')
        state = load_run_state(self.root, self.identifier)
        self.assertEqual(state['attempt'], 2)
        self.assertEqual(state['failures'], [])
        self.assertEqual(len(calls), 1)

    def test_same_tier_scope_growth_replans_once_and_keeps_candidate(self):
        from dataclasses import replace
        from storyloop.project_loop import run_change, load_run_state
        from tests.test_routing_planning import answer
        services = self.services(role='none')
        implement = services.implement
        calls = []
        def expanded(project, context):
            result = implement(project, context)
            (project / 'app/extra.py').write_text('EXTRA = True\n')
            return result
        def design(project, context):
            calls.append(context)
            result = answer(self.change['product'])
            if context.get('replanning', {}).get('origin') == 'L3':
                result['options'][0]['creates'].append('app/extra.py')
            return result
        services = replace(services, implement=expanded, design=design)
        result = run_change(self.root, self.identifier, services)
        self.assertEqual(result['status'], 'PASSED')
        state = load_run_state(self.root, self.identifier)
        self.assertEqual(state['routing']['effective_tier'], 'standard')
        self.assertEqual(state['attempt'], 2)
        self.assertEqual(state['failures'], [])
        self.assertEqual(len(calls), 2)
        self.assertEqual((self.root / 'app/extra.py').read_bytes(), b'EXTRA = True\n')
        self.assertEqual(len([h for h in state['routing']['history'] if h['stage'] == 'L3']), 1)


class AdaptiveLoopTests(unittest.TestCase):
    def test_second_attempt_upgrade_extends_only_tier_budget(self):
        from unittest.mock import patch
        from storyloop.project_loop import _attempts
        from storyloop.routing_state import advance_tier
        change = {'approval': {'tier': 'light'}, 'intake': {'decision': {'reasons': []}},
                  'product': {'assessment': {'scope': {}}}}
        for explicit, expected in [(False, [1, 2, 3, 4]), (True, [1, 2])]:
            config = {'max_attempts': 2}
            state = {'attempt': 0, 'routing': open_routing(change, DEFAULT_POLICY,
                                                         config if explicit else {})}
            calls = []
            def attempt(project, change, state, services, config):
                calls.append(state['attempt'])
                if state['attempt'] == 2:
                    advance_tier(state, {'tier': 'heavy', 'reasons': []}, 'L3', 'upgrade', config)
            with patch('storyloop.project_loop.save_run'), patch('storyloop.project_loop.pause'), \
                    patch('storyloop.project_loop._run_attempt', side_effect=attempt):
                _attempts(None, change, state, None, config, 1)
            self.assertEqual(calls, expected)
