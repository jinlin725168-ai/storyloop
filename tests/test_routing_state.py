"""Versioned run selection and quality budgets."""
from tests.test_structured_output import passing_review
from tests.test_routing_planning import answer
from storyloop.design_approval import approve_design
from copy import deepcopy
import unittest
from storyloop.models import StoryLoopError
from storyloop.routing_policy import DEFAULT_POLICY
from storyloop.routing_state import is_routed, open_routing, resolve_budget


class BudgetTests(unittest.TestCase):
    def test_tier_defaults_and_explicit_overrides(self):
        for tier, limit in [('light', 2), ('standard', 3), ('heavy', 4)]:
            self.assertEqual(resolve_budget({}, tier), {'limit': limit, 'source': 'tier'})
            self.assertEqual(resolve_budget({}, tier, legacy=True),
                             {'limit': 3, 'source': 'legacy'})
            for explicit in (1, 3, 5):
                self.assertEqual(resolve_budget({'max_attempts': explicit}, tier),
                                 {'limit': explicit, 'source': 'explicit'})

    def test_bootstrap_preserves_explicit_override(self):
        import tempfile
        from pathlib import Path
        from tests.routing_support import readiness_repository
        from storyloop.baseline import load_project_config
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            readiness_repository(root, max_attempts=5)
            self.assertEqual(load_project_config(root)['max_attempts'], 5)

    def test_invalid_explicit_budget(self):
        for value in (True, False, 0, -1, 1.5, '3', None):
            with self.subTest(value=value), self.assertRaises(StoryLoopError):
                resolve_budget({'max_attempts': value}, 'light')

    def test_existing_run_always_controls_protocol(self):
        change = {'routing_version': 1}
        self.assertTrue(is_routed(change))
        self.assertFalse(is_routed(change, {}))
        self.assertFalse(is_routed(change, {'routed': {'verdict': 'fail'}}))
        self.assertTrue(is_routed({}, {'routing': {'version': 1}}))
        self.assertFalse(is_routed({}))

    def test_resume_budget_increase_decrease_and_defaults(self):
        from storyloop.routing_state import run_config
        state = {'attempt': 3, 'routing': {'version': 1, 'effective_tier': 'heavy',
                                         'limit': 4, 'budget_source': 'tier',
                                         'policy': deepcopy(DEFAULT_POLICY)}}
        for limit in (1, 5):
            config = run_config({'max_attempts': limit}, {}, state)
            self.assertEqual(config['max_attempts'], limit)
            self.assertEqual(state['attempt'], 3)
            self.assertEqual(config['routing'], DEFAULT_POLICY)
        self.assertEqual(run_config({}, {}, {})['max_attempts'], 3)
        with self.assertRaises(StoryLoopError):
            run_config({'max_attempts': False}, {}, state)

    def test_open_freezes_approved_manual_floor_and_inputs(self):
        change = {'approval': {'tier': 'heavy'}, 'intake': {
            'decision': {'tier': 'light', 'reasons': []}},
            'product': {'assessment': {'scope': {'touched': ['app.py']}}}}
        policy = deepcopy(DEFAULT_POLICY)
        result = open_routing(change, policy, {})
        self.assertEqual(result['initial_tier'], 'heavy')
        self.assertEqual(result['effective_tier'], 'heavy')
        self.assertEqual(result['limit'], 4)
        policy['guard_paths'].append('app.py')
        change['product']['assessment']['scope']['touched'].clear()
        self.assertEqual(result['policy']['guard_paths'], [])
        self.assertEqual(result['scope']['touched'], ['app.py'])


class RunRoutingTests(unittest.TestCase):
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
        readiness_repository(self.root, module_roots=["app", "modules"], test_roots=["tests"])
        ready = inspect_readiness(self.root)
        approve_readiness(self.root, ready['digest'], 'Local tests verified')
        change = create_change(self.root, 'orders', assessed_product())
        self.change = approve_change(self.root, change['change_id'], tier='heavy')
        self.identifier = change['change_id']

    def test_new_init_omits_budget_and_stale_gate_creates_no_run(self):
        from storyloop.baseline import load_project_config
        from storyloop.storage import write_json
        from storyloop.project_loop import run_change
        config = load_project_config(self.root)
        self.assertNotIn('max_attempts', config)
        config['routing'] = {**DEFAULT_POLICY, 'manual_ratio': .5}
        write_json(self.root / '.storyloop/config.json', config)
        with self.assertRaises(StoryLoopError):
            run_change(self.root, self.identifier)
        self.assertFalse((self.root / '.storyloop/state/runs' / (self.identifier + '.json')).exists())

    def test_resume_freezes_policy_rechecks_budget_and_passed_fast_return(self):
        from unittest.mock import patch
        from storyloop.project_loop import LoopServices, run_change, load_run_state
        from storyloop.baseline import load_project_config
        from storyloop.storage import write_json
        from tests.test_agent_unavailable import implement_after, unavailable
        from tests.test_project_loop import smoke_payload
        calls = []
        implement = implement_after(1, calls)
        budgets = []
        def run_implement(project, context):
            budgets.append(context['max_attempts'])
            return implement(project, context)
        def design(project, context):
            plan = answer()
            plan['options'][0]['creates'] = ['modules/orders/service.py']
            return plan
        def characterize(project, context):
            (project / 'tests/test_lock.py').write_text(
                'import unittest\nfrom app.service import serve\n'
                'class Lock(unittest.TestCase):\n'
                '    def test_behavior(self):\n'
                '        self.assertEqual(serve(), "ok")\n')
            return {'summary': 'Lock serve', 'touched': ['app/service.py'],
                    'tests': ['tests/test_lock.py'], 'questions': []}
        services = LoopServices(lambda p, c: smoke_payload(self.identifier),
                                run_implement, passing_review, characterize=characterize,
                                design=design)
        paused = run_change(self.root, self.identifier, services)
        approve_design(self.root, self.identifier, paused['plan_id'])
        with self.assertRaises(StoryLoopError) as raised:
            run_change(self.root, self.identifier, services)
        self.assertEqual(raised.exception.code, 'AGENT_UNAVAILABLE')
        state = load_run_state(self.root, self.identifier)
        self.assertEqual(state['routing']['limit'], 4)
        self.assertEqual(state['attempt'], 0)
        config = load_project_config(self.root)
        config['routing'] = {**DEFAULT_POLICY, 'manual_ratio': .5}
        config['max_attempts'] = 5
        write_json(self.root / '.storyloop/config.json', config)
        with patch('storyloop.project_loop.require_intake', side_effect=AssertionError('resume intake')):
            self.assertEqual(run_change(self.root, self.identifier, services)['status'], 'PASSED')
        state = load_run_state(self.root, self.identifier)
        self.assertEqual(state['routing']['policy'], DEFAULT_POLICY)
        self.assertEqual(state['routing']['limit'], 5)
        self.assertEqual(state['routing']['budget_source'], 'explicit')
        self.assertEqual(calls, [1, 1])
        self.assertEqual(budgets, [4, 5])
        with patch('storyloop.project_loop.load_project_config', side_effect=AssertionError('fast return')):
            self.assertEqual(run_change(self.root, self.identifier, services)['status'], 'PASSED')
