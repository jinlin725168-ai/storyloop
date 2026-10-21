"""Generation constraints must agree with the deferred lesson validator."""
import copy
import unittest

from storyloop.project_agents import conform
from storyloop.retrospective import _validate_proposal
from storyloop.role_schemas import schema_for, schema_errors, wire_schema
from tests.lesson_support import cleanup_repo, initialized_project, sample


class RetrospectiveProtocolTests(unittest.TestCase):
    def setUp(self):
        self.schema = schema_for('review', {'mode': 'retrospective'})
        self.wire = wire_schema(self.schema)
        self.proposal_schema = self.wire['properties']['lessons']['items']

    def proposal(self, **overrides):
        entry = sample()
        entry.pop('id')
        body = entry.pop('body')
        return dict(action='create', target=None, entry={**entry, **overrides},
                    body=body, why_needed='Avoid an incorrect retry assumption',
                    evidence=['README.md:1'], landing=None)

    def test_wire_rejects_overlong_summaries_at_generation_boundary(self):
        self.assertEqual(schema_errors(self.proposal(summary='界' * 120), self.proposal_schema), [])
        self.assertTrue(schema_errors(self.proposal(summary='界' * 121), self.proposal_schema))

    def test_prose_null_write_matches_a_native_nullable_variant(self):
        landing = self.proposal_schema['properties']['landing']
        self.assertEqual(schema_errors(None, landing), [])
        self.assertTrue(any(not schema_errors(None, branch) for branch in landing.get('anyOf', [])))

    def test_wire_requires_non_prose_landing_references(self):
        for strength in ('knowledge', 'check', 'config'):
            with self.subTest(strength=strength):
                self.assertTrue(schema_errors(self.proposal(strength=strength), self.proposal_schema))

    def test_reference_and_write_instruction_accept_different_fields(self):
        reference = {'kind': 'foundation', 'path': 'openspec/foundation/runtime.md', 'anchor': 'retries'}
        operation = {'kind': 'foundation', 'path': reference['path'], 'append': 'Retry policy'}
        proposal = self.proposal(strength='knowledge', landings=[reference])
        proposal['landing'] = operation
        self.assertEqual(schema_errors(proposal, self.proposal_schema), [])
        for field, bad in (('reference', operation), ('operation', reference)):
            with self.subTest(field=field):
                broken = copy.deepcopy(proposal)
                if field == 'reference':
                    broken['entry']['landings'] = [bad]
                else:
                    broken['landing'] = bad
                self.assertTrue(schema_errors(broken, self.proposal_schema))

    def test_nullable_wire_landings_normalize_to_engine_accepted_proposals(self):
        project = initialized_project()
        self.addCleanup(cleanup_repo, project)
        cases = [
            ('knowledge', {'kind': 'foundation', 'path': 'openspec/foundation/runtime.md'},
             {'kind': 'foundation', 'path': 'openspec/foundation/runtime.md', 'append': 'Retry policy'}),
            ('knowledge', {'kind': 'principles'},
             {'kind': 'principles', 'principle': {'id': 'P-001', 'text': 'Preserve data'},
              'change': 'Document preservation'}),
            ('check', {'kind': 'check_command', 'argv': ['python', '-m', 'unittest']},
             {'kind': 'check_command', 'argv': ['python', '-m', 'unittest']}),
        ]
        entry_schema = self.proposal_schema['properties']['entry']
        reference_properties = entry_schema['properties']['landings']['items']['properties']
        operation_properties = self.proposal_schema['properties']['landing']['properties']
        for strength, reference, operation in cases:
            with self.subTest(strength=strength, kind=reference['kind']):
                proposal = self.proposal(strength=strength, landings=[reference])
                proposal['landing'] = operation
                proposal['entry']['landings'] = [{key: reference.get(key) for key in reference_properties}]
                proposal['landing'] = {key: operation.get(key) for key in operation_properties}
                reply = dict(lessons=[proposal], errata=[], uncovered_concepts=[], nothing_to_learn=None)
                self.assertEqual(schema_errors(reply, self.wire), [])
                normalized, errors = conform(reply, self.schema)
                self.assertEqual(errors, [])
                metadata, _ = _validate_proposal(project, normalized['lessons'][0], set())
                self.assertEqual(metadata['entry']['landings'], [reference])
                self.assertEqual(metadata['landing'], operation)

    def test_invalid_siblings_are_dropped_and_valid_knowledge_can_be_approved(self):
        from storyloop.base_upgrade import upgrade_baseline
        from storyloop.lesson_landing import approve_draft
        from storyloop.retrospective import _drafts
        from storyloop.drafts import load_draft
        project = initialized_project()
        self.addCleanup(cleanup_repo, project)
        valid = self.proposal(strength='knowledge', landings=[{
            'kind': 'foundation', 'path': 'openspec/foundation/runtime.md'}])
        valid['landing'] = {'kind': 'foundation', 'path': 'openspec/foundation/runtime.md',
                            'append': 'Retry policy'}
        invalid = [self.proposal(summary='x' * 121), self.proposal(strength='knowledge')]
        reply = dict(lessons=[*invalid, valid], errata=[], uncovered_concepts=[], nothing_to_learn=None)
        normalized, errors = conform(reply, self.schema)
        self.assertEqual(errors, [])  # deferred item errors never block a valid sibling
        result = {'drafts': []}
        _drafts(project, 'FEAT-1', normalized, {'lessons': []}, result, proposer='call-test')
        self.assertEqual(len(result['drafts']), 1)
        self.assertEqual(len(result['dropped']), 2)
        ref = result['drafts'][0]
        self.assertEqual(load_draft(project, ref)['proposer'], 'call-test')
        approved = approve_draft(project, ref)
        self.assertEqual(approved['lesson_id'], 'L-0001')
        self.assertEqual(upgrade_baseline(project, approved['base_proposal'])['status'], 'BASELINE_UPGRADED')
        self.assertIn('Retry policy', (project / 'openspec/foundation/runtime.md').read_text())


if __name__ == '__main__':
    unittest.main()
