"""New-protocol intake and direct API gates."""
import tempfile
import unittest
from pathlib import Path
from copy import deepcopy
from storyloop.intake import assess_intake, exploration_required
from storyloop.approvals import approve_change
from storyloop.changes import create_change, revise_change, contract_digest
from storyloop.models import StoryLoopError
from storyloop.routing_policy import DEFAULT_POLICY
from storyloop.readiness import inspect_readiness, approve_readiness
from storyloop.storage import write_json
from tests.routing_support import readiness_repository, signals
from tests.support import product
from tests.test_baseline_behavior import git


def assessed_product():
    value = product()
    evidence = {'locations': ['app/service.py:1'], 'search_scope': ['app']}
    value['assessment'] = {
        'verifiable_goal': True,
        'scope': {'touched': ['app/service.py'], 'creates': [], 'interfaces': [],
                  'symbols': [{'path': 'app/service.py', 'symbol': 'serve'}]},
        'risks': {key: {'value': 'no', 'evidence': deepcopy(evidence)}
                  for key in signals()['risks']},
        'criteria': [{'story_id': 'ST-1', 'criterion_id': 'AC-1',
                      'feasibility': 'local_automated', 'existence': 'absent',
                      'method': {'commands': [['{python}', '-m', 'unittest']],
                                 'steps': [], 'expected': 'Reject overlapping reservations'},
                      'evidence': evidence}]}
    return value


class IntakeTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        readiness_repository(self.root)
        self.commit = git(self.root, 'rev-parse', 'HEAD').strip()

    def ready(self):
        state = inspect_readiness(self.root)
        approve_readiness(self.root, state['digest'], 'Local tests cover this change')

    def assess(self, value):
        return assess_intake(self.root, value, self.commit, DEFAULT_POLICY)

    def test_exploration_boundary(self):
        self.assertFalse(exploration_required(True, 1, 1))
        self.assertTrue(exploration_required(True, 2, 2))
        self.assertFalse(exploration_required(True, 2, 3))
        self.assertTrue(exploration_required(False, 0, 1))

    def test_default_draft_missing_assessment_cannot_bypass(self):
        state = create_change(self.root, 'idea', product())
        self.assertEqual(state['routing_version'], 1)
        self.assertEqual(state['intake']['status'], 'NEEDS_INPUT')
        self.ready()
        with self.assertRaisesRegex(StoryLoopError, 'NEEDS_INPUT'):
            approve_change(self.root, state['change_id'])

    def test_readiness_blocks_direct_approval(self):
        state = create_change(self.root, 'idea', assessed_product())
        with self.assertRaisesRegex(StoryLoopError, 'not ready'):
            approve_change(self.root, state['change_id'])

    def test_approval_binds_intake_and_rejects_light_override(self):
        self.ready()
        state = create_change(self.root, 'idea', assessed_product())
        with self.assertRaises(StoryLoopError):
            approve_change(self.root, state['change_id'], tier='light')
        approved = approve_change(self.root, state['change_id'], tier='standard')
        self.assertEqual(approved['approval']['tier'], 'standard')
        self.assertEqual(approved['approval']['intake_digest'], contract_digest(approved['intake']))
        self.assertEqual(approve_change(self.root, state['change_id']), approved)

    def test_unknown_partial_and_infeasible_stop(self):
        for field, value, expected in [('existence', 'unknown', 'NEEDS_INPUT'),
                                       ('existence', 'partial', 'NEEDS_INPUT'),
                                       ('existence', 'exists', 'NEEDS_INPUT'),
                                       ('feasibility', 'infeasible', 'ORACLE_INFEASIBLE')]:
            with self.subTest(value=value):
                item = assessed_product()
                item['assessment']['criteria'][0][field] = value
                self.assertEqual(self.assess(item)['status'], expected)

    def test_invalid_criterion_records_and_missing_proof(self):
        for mutate in (lambda a: a['criteria'].clear(),
                       lambda a: a['criteria'].append(deepcopy(a['criteria'][0])),
                       lambda a: a['criteria'][0].update(criterion_id='AC-999'),
                       lambda a: a['criteria'][0].update(evidence={'locations': [], 'search_scope': []}),
                       lambda a: a['criteria'][0].update(feasibility='manual')):
            item = assessed_product()
            mutate(item['assessment'])
            with self.assertRaises(StoryLoopError):
                self.assess(item)

    def test_engine_no_cannot_erase_risk_unknown_or_yes(self):
        for risk in ('unknown', 'yes'):
            item = assessed_product()
            item['assessment']['risks']['sensitive']['value'] = risk
            result = self.assess(item)
            self.assertEqual(result['status'], 'NEEDS_INPUT' if risk == 'unknown' else 'READY')
            if risk == 'yes':
                self.assertEqual(result['decision']['tier'], 'heavy')

    def test_bad_source_location_rejected(self):
        item = assessed_product()
        item['assessment']['criteria'][0]['evidence']['locations'] = ['app/service.py:999']
        with self.assertRaises(StoryLoopError):
            self.assess(item)

    def test_profile_and_policy_changes_invalidate_bindings(self):
        from storyloop.storage import load_json
        for field in ('profile', 'routing'):
            with self.subTest(field=field):
                config_path = self.root / '.storyloop/config.json'
                original = load_json(config_path)
                self.ready()
                state = create_change(self.root, 'idea', assessed_product())
                approve_change(self.root, state['change_id'])
                changed = deepcopy(original)
                if field == 'profile':
                    changed['profile']['test_roots'].append('other-tests')
                else:
                    changed['routing'] = {**changed.get('routing', {}), 'manual_ratio': .5}
                write_json(config_path, changed)
                self.ready()
                with self.assertRaisesRegex(StoryLoopError, 'stale'):
                    approve_change(self.root, state['change_id'])
                write_json(config_path, original)

    def test_head_change_invalidates_source_evidence(self):
        self.ready()
        state = create_change(self.root, 'idea', assessed_product())
        approve_change(self.root, state['change_id'])
        git(self.root, 'commit', '--allow-empty', '-qm', 'new source')
        self.ready()
        with self.assertRaisesRegex(StoryLoopError, 'stale'):
            approve_change(self.root, state['change_id'])

    def test_legacy_approved_digest_stable_and_explicit_revision_upgrades(self):
        from tests.routing_support import legacy_create_change
        state = legacy_create_change(self.root, 'legacy', product())
        approved = approve_change(self.root, state['change_id'])
        self.assertNotIn('intake_digest', approved['approval'])
        self.assertEqual(approved['approval']['contract_digest'], contract_digest(product()))
        revised = revise_change(self.root, state['change_id'], assessed_product())
        self.assertEqual(revised['routing_version'], 1)
        self.assertEqual(revised['intake']['status'], 'READY')
        self.assertIsNone(revised['approval'])

    def test_manual_and_unavailable_methods(self):
        item = assessed_product()
        criterion = item['assessment']['criteria'][0]
        criterion['feasibility'] = 'manual'
        criterion['method']['steps'] = ['Submit an overlapping reservation; inspect rejection']
        self.assertEqual(self.assess(item)['decision']['tier'], 'heavy')
        criterion['feasibility'] = 'local_automated'
        criterion['method']['commands'] = [['nonexistent-storyloop-test-binary']]
        self.assertEqual(self.assess(item)['status'], 'ORACLE_INFEASIBLE')

    def test_product_readonly_boundary_rejects_writes(self):
        from storyloop.project_loop_guards import read_only_call
        with self.assertRaises(StoryLoopError):
            read_only_call(self.root, 'intake-product',
                           lambda: (self.root / 'app/service.py').write_text('changed'))

    def test_supersede_is_new_protocol(self):
        supersedes = {'change_id': 'FEAT-old', 'message': 'new contract', 'reason': None,
                      'blocker': None, 'source_branch': 'main', 'design_summary': None}
        state = create_change(self.root, 'replacement', assessed_product(), supersedes=supersedes)
        self.assertEqual(state['routing_version'], 1)
        self.assertEqual(state['intake']['decision']['tier'], 'light')

    def test_assessment_schema_nullable_compatibility(self):
        from storyloop.role_schemas import SCHEMAS, normalize, schema_errors
        schema = SCHEMAS['product']
        self.assertEqual(schema_errors(assessed_product(), schema), [])
        self.assertEqual(normalize({**product(), 'assessment': None}, schema), product())

    def test_worktree_cannot_substitute_commit_evidence(self):
        item = assessed_product()
        (self.root / 'app/service.py').write_text('a\n' * 1000)
        item['assessment']['criteria'][0]['evidence']['locations'] = ['app/service.py:999']
        with self.assertRaises(StoryLoopError):
            self.assess(item)

    def test_existing_context_with_new_criterion_remains_feature(self):
        item = assessed_product()
        criterion = deepcopy(item['stories'][0]['acceptance'][0])
        criterion['id'] = 'AC-2'
        item['stories'][0]['acceptance'].append(criterion)
        record = deepcopy(item['assessment']['criteria'][0])
        record['criterion_id'] = 'AC-2'
        item['assessment']['criteria'].append(record)
        item['assessment']['criteria'][0]['existence'] = 'exists'
        self.assertEqual(self.assess(item)['status'], 'READY')
        self.assertNotIn('nature', item)
