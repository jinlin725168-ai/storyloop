"""Heavy consistency protocol, routing and bound cache regressions."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import unittest

from storyloop.consistency import consistency_status, validate_consistency, ensure_consistency
from storyloop.consistency_locations import validate_plan_location
from storyloop.models import StoryLoopError
from storyloop.project_loop import load_run_state, run_change
from storyloop.project_loop_support import save_run
from storyloop.role_schemas import schema_for, schema_errors
from tests import test_design_approval
from tests.test_routing_planning import answer


def coherent(project=None, context=None):
    return {'verdict': 'coherent', 'summary': 'Contract, oracle and plan agree',
            'questions': [], 'findings': []}


def finding(target='design', location='plan:main/constraints/0', blocking=True):
    return {'target': target, 'criterion_id': 'AC-1', 'location': location,
            'blocking': blocking, 'message': 'The referenced obligation is inconsistent'}


class ConsistencyTests(unittest.TestCase):
    def test_contract_blocker_has_priority_without_losing_findings(self):
        findings = [finding(target) for target in ('design', 'oracle', 'contract')]
        self.assertEqual(consistency_status(findings), 'NEEDS_SPEC_FIX')
        self.assertEqual(len(findings), 3)
        self.assertEqual(consistency_status(findings[:2]), 'ORACLE_DEFECT')
        self.assertEqual(consistency_status(findings[:1]), 'DESIGN_REVISION_REQUIRED')
        self.assertEqual(consistency_status([]), 'COHERENT')
        self.assertEqual(consistency_status([finding(blocking=False)]), 'COHERENT')

    def test_restricted_plan_locations(self):
        plan = answer()
        for location in ('plan:main/summary', 'plan:main/constraints/0/rule',
                         'plan:main/regression/0', 'plan:main/migration/reason'):
            self.assertTrue(validate_plan_location(plan, location), location)
        for location in ('plan:absent/summary', 'plan:main/risks', 'plan:main/constraints/99',
                         'plan:main/constraints/-1', 'plan:main/constraints/00',
                         'plan:main/../summary', 'app/service.py:1', 'plan:main/summary/0'):
            self.assertFalse(validate_plan_location(plan, location), location)

    def test_native_review_schema_selects_consistency_only(self):
        schema = schema_for('review', {'mode': 'consistency'})
        self.assertEqual(schema_errors(coherent(), schema), [])
        self.assertTrue(schema_errors(coherent(), schema_for('review', {})))
        invalid = coherent()
        invalid['findings'] = [finding(blocking=1)]
        self.assertTrue(schema_errors(invalid, schema))


class ConsistencyGateTests(unittest.TestCase):
    setUp = test_design_approval.DesignApprovalTests.setUp
    start = test_design_approval.DesignApprovalTests.start
    approve = test_design_approval.DesignApprovalTests.approve

    def prepared(self):
        self.start()
        self.approve()
        self.state = load_run_state(self.root, self.identifier)
        self.services = replace(self.services, review=coherent)
        return self.state

    def test_actual_gate_precedes_characterization_and_caches_same_binding(self):
        self.prepared()
        calls = []
        def review(project, context):
            calls.append(context)
            self.assertTrue((project / context['plan_path']).is_file())
            return coherent()
        self.services = replace(self.services, review=review)
        self.assertIsNone(ensure_consistency(self.root, self.change, self.state, self.services))
        self.assertIsNone(ensure_consistency(self.root, self.change, self.state, self.services))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]['mode'], 'consistency')
        self.assertEqual(self.state['attempt'], 0)

    def test_invalid_reference_repaired_once_then_protocol_error(self):
        self.prepared()
        calls = []
        def review(project, context):
            calls.append(context)
            return {**coherent(), 'verdict': 'blocked',
                    'findings': [finding(location='plan:main/constraints/999')]}
        self.services = replace(self.services, review=review)
        with self.assertRaises(StoryLoopError):
            ensure_consistency(self.root, self.change, self.state, self.services)
        self.assertEqual(len(calls), 2)
        self.assertIn('validation_errors', calls[1])
        self.assertNotIn('consistency', self.state['planning'])

    def test_reject_contradictions_and_invalid_criterion(self):
        self.prepared()
        cases = [{**coherent(), 'findings': [finding()]},
                 {**coherent(), 'verdict': 'blocked'},
                 {**coherent(), 'verdict': 'needs_input'},
                 {**coherent(), 'questions': ['ambiguous']},
                 {**coherent(), 'verdict': 'blocked',
                  'findings': [{**finding(), 'criterion_id': 'missing'}]},
                 {**coherent(), 'verdict': 'blocked',
                  'findings': [finding('oracle', 'app/service.py:1')]}]
        for response in cases:
            with self.subTest(response=response), self.assertRaises(StoryLoopError):
                validate_consistency(response, self.root, self.change, self.state)

    def test_read_only_review_protects_routing_artifact_even_in_scratch(self):
        self.prepared()
        from storyloop.baseline import load_project_config
        from storyloop.storage import write_json
        config = load_project_config(self.root)
        config['profile']['scratch_paths'] = ['openspec/changes']
        write_json(self.root / '.storyloop/config.json', config)
        def review(project, context):
            (project / context['plan_path']).write_text('{}')
            return coherent()
        self.services = replace(self.services, review=review)
        with self.assertRaises(StoryLoopError) as caught:
            ensure_consistency(self.root, self.change, self.state, self.services)
        self.assertEqual(caught.exception.code, 'WORKSPACE_TAINTED')
        self.assertNotIn('consistency', self.state['planning'])

    def test_mixed_findings_use_existing_routed_repair_stop(self):
        self.prepared()
        from storyloop.smoke import suite_relative
        suite = suite_relative(self.root, self.identifier)
        oracle = next(path for path in (self.root / suite).rglob('*.py'))
        findings = [finding(), finding('oracle', oracle.relative_to(self.root).as_posix() + ':1'),
                    finding('contract', 'app/service.py:1')]
        self.services = replace(self.services, review=lambda p, c: {
            **coherent(), 'verdict': 'blocked', 'findings': findings})
        result = run_change(self.root, self.identifier, self.services)
        self.assertEqual(result['status'], 'NEEDS_SPEC_FIX')
        state = load_run_state(self.root, self.identifier)
        self.assertEqual(state['routed']['target'], 'contract')
        self.assertEqual(len(state['planning']['consistency']['findings']), 3)
        self.assertEqual(len(state['failures'][-1]['review']['findings']), 3)
        self.assertEqual(self.calls, [])
        with self.assertRaisesRegex(StoryLoopError, 'overrule'):
            run_change(self.root, self.identifier, self.services)

    def test_binding_drift_drops_cache_preserves_valid_locks(self):
        self.prepared()
        ensure_consistency(self.root, self.change, self.state, self.services)
        locks = {'status': 'GREEN', 'locks': {'app/service.py': 'test'}}
        self.state['characterization'] = deepcopy(locks)
        from storyloop.smoke import suite_relative
        suite = self.root / suite_relative(self.root, self.identifier)
        path = next(suite.rglob('*.py'))
        path.write_text(path.read_text() + '\n# changed suite\n')
        result = ensure_consistency(self.root, self.change, self.state, self.services)
        self.assertEqual(result['status'], 'DESIGN_REVISION_REQUIRED')
        self.assertNotIn('consistency', self.state['planning'])
        self.assertEqual(self.state['characterization'], locks)
        self.assertNotIn('approval', self.state['planning']['plan'])

    def test_coherent_allows_real_characterization_and_implementation(self):
        self.prepared()
        def characterize(project, context):
            (project / 'tests/test_lock.py').write_text(
                'import unittest\nfrom app.service import serve\n'
                'class Lock(unittest.TestCase):\n'
                '    def test_behavior(self):\n'
                "        self.assertEqual(serve(), 'ok')\n")
            return {'summary': 'Lock existing behavior', 'touched': ['app/service.py'],
                    'tests': ['tests/test_lock.py'], 'questions': []}
        services = replace(self.services, characterize=characterize,
                           implement=lambda p, c: {'summary': 'Reached implementation',
                                                   'questions': ['Stop at implementation']})
        self.assertEqual(run_change(self.root, self.identifier, services)['status'], 'NEEDS_INPUT')
        state = load_run_state(self.root, self.identifier)
        self.assertEqual(state['characterization']['status'], 'GREEN')
        self.assertEqual(state['attempt'], 1)
        from tests.test_baseline_behavior import git
        self.assertEqual(git(self.root, 'status', '--porcelain', '--',
                             f'openspec/changes/{self.identifier}/routing-plan.json'), '')

    def test_answer_changes_invalidate_consistency_without_discarding_locks(self):
        self.prepared()
        calls = []
        self.services = replace(self.services, review=lambda p, c: calls.append(c) or coherent())
        ensure_consistency(self.root, self.change, self.state, self.services)
        from storyloop.answers import record_answers
        self.state.update(status='NEEDS_INPUT', questions=['Clarify constraint'])
        self.state['characterization'] = {'status': 'GREEN'}
        save_run(self.root, self.state)
        record_answers(self.root, self.identifier, ['Keep existing constraint'])
        ensure_consistency(self.root, self.change, self.state, self.services)
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.state['characterization'], {'status': 'GREEN'})

    def test_acceptance_repair_reproves_red_then_requires_new_l2_approval(self):
        self.prepared()
        from storyloop.smoke import suite_relative
        from storyloop.acceptance_repair import repair_smoke
        from tests.test_project_loop import smoke_payload
        path = next((self.root / suite_relative(self.root, self.identifier)).rglob('*.py'))
        response = {**coherent(), 'verdict': 'blocked', 'findings': [
            finding('oracle', path.relative_to(self.root).as_posix() + ':1')]}
        self.services = replace(self.services, review=lambda p, c: response)
        self.assertEqual(run_change(self.root, self.identifier, self.services)['status'], 'ORACLE_DEFECT')
        def repaired(project, context):
            payload = smoke_payload(self.identifier)
            payload['files'][0]['content'] += '\n# repaired oracle reference\n'
            return payload
        repair_smoke(self.root, self.identifier, 'Correct oracle obligation',
                     replace(self.services, generate_smoke=repaired))
        state = load_run_state(self.root, self.identifier)
        self.assertNotIn('plan', state['planning'])
        self.assertNotIn('consistency', state['planning'])
        self.assertEqual(state['attempt'], 0)
        self.assertEqual(run_change(self.root, self.identifier, self.services)['status'],
                         'DESIGN_APPROVAL_REQUIRED')
        state = load_run_state(self.root, self.identifier)
        self.assertNotIn('approval', state['planning']['plan'])
        self.assertEqual(self.calls, [])


    def test_overrule_preserves_remaining_oracle_then_design_blockers(self):
        self.prepared()
        from storyloop.smoke import suite_relative
        from storyloop.project_loop_failures import apply_overrule
        path = next((self.root / suite_relative(self.root, self.identifier)).rglob('*.py'))
        findings = [finding('contract', 'app/service.py:1'),
                    finding('oracle', path.relative_to(self.root).as_posix() + ':1'), finding()]
        self.services = replace(self.services, review=lambda p, c: {
            **coherent(), 'verdict': 'blocked', 'findings': findings})
        ensure_consistency(self.root, self.change, self.state, self.services)
        self.assertEqual(apply_overrule(self.root, self.state, 'Contract is intentional')['status'],
                         'ORACLE_DEFECT')
        self.assertEqual(apply_overrule(self.root, self.state, 'Oracle is correct')['status'],
                         'DESIGN_REVISION_REQUIRED')
        actual = self.state['planning']['consistency']['findings']
        self.assertEqual([item['blocking'] for item in actual], [False, False, True])
        self.assertEqual(len(self.state['overrules']), 2)

    def test_standard_dependency_gap_stops_before_any_review_or_implementation(self):
        self.prepared()
        from storyloop.planning import validate_plan
        response = answer()
        response['options'][0]['dependencies'] = [{'ecosystem': 'npm', 'name': 'demo',
            'version': '1.0', 'verification': {'check_index': 0}}]
        self.state['planning']['plan'] = validate_plan(response, self.root, self.change, self.state)
        self.state['routing']['effective_tier'] = 'standard'
        self.services = replace(self.services, review=lambda p, c: self.fail('Standard paid review'))
        result = ensure_consistency(self.root, self.change, self.state, self.services)
        self.assertEqual(result['status'], 'NEEDS_INPUT')
        self.assertEqual(result['phase'], 'planning')
        self.assertNotIn('consistency', self.state['planning'])
        self.assertEqual(self.state['attempt'], 0)


class PrincipleApprovalTests(unittest.TestCase):
    def setUp(self):
        import json
        import tempfile
        from storyloop.baseline import bootstrap_project
        from storyloop.readiness import inspect_readiness, approve_readiness
        from storyloop.changes import create_change
        from storyloop.approvals import approve_change
        from tests.routing_support import routing_repository
        from tests.test_baseline_behavior import git
        from tests.test_intake import assessed_product
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        routing_repository(self.root)
        path = self.root / 'openspec/principles.json'
        path.parent.mkdir()
        path.write_text(json.dumps({'version': 1, 'principles': [{'id': 'P1', 'text': 'Reuse modules'}]}))
        git(self.root, 'add', 'openspec/principles.json')
        git(self.root, 'commit', '-qm', 'principles')
        bootstrap_project(self.root, {'module_roots': ['app'], 'test_roots': ['tests'],
            'unit_commands': [['{python}', '-m', 'unittest', 'discover', '-s', 'tests']]})
        ready = inspect_readiness(self.root)
        approve_readiness(self.root, ready['digest'], 'Verified local fixture')
        change = create_change(self.root, 'orders', assessed_product())
        self.change = approve_change(self.root, change['change_id'], tier='standard')
        self.identifier = change['change_id']

    def test_standard_deviation_requires_approval_before_paid_review(self):
        from storyloop.project_loop import LoopServices
        from tests.test_project_loop import smoke_payload
        response = answer()
        response['options'][0]['principle_checks'] = [
            {'principle_id': 'P1', 'verdict': 'deviates', 'reason': 'Explicit justified exception'}]
        calls = []
        def forbidden(*args):
            calls.append('called')
            self.fail('No implementation, characterization or paid review before approval')
        services = LoopServices(lambda p, c: smoke_payload(self.identifier), forbidden, forbidden,
                                characterize=forbidden, design=lambda p, c: response)
        result = run_change(self.root, self.identifier, services)
        self.assertEqual(result['status'], 'DESIGN_APPROVAL_REQUIRED')
        state = load_run_state(self.root, self.identifier)
        self.assertEqual(result['principle_deviations'][0]['principle_id'], 'P1')
        self.assertEqual(state['routing']['initial_tier'], 'standard')
        self.assertEqual(state['routing']['effective_tier'], 'heavy')
        self.assertEqual(state['planning']['assessment']['signals']['risks']['principle_deviation'], 'yes')
        self.assertEqual(calls, [])
        self.assertEqual(state['attempt'], 0)
