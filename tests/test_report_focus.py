import json
import shlex
import unittest

from storyloop.report_focus import build_focus


class FocusTests(unittest.TestCase):
    def fixture(self):
        change = {'product': {'consumers': [{'id': 'C', 'path': 'z.py', 'in_scope': True}],
                              'assessment': {'risks': {'race': {'value': 'yes'}}}}}
        report = {'review': {'summary': 'INJECTED PROSE'}, 'traceability': [
            {'criterion': {'id': 'AC1'}, 'tests': [{'id': 'T', 'command': ['python', 'a b.py', '$(oops)'],
                                                   'result': {'status': 'PASS'}}]}]}
        evidence = {'run': {'routing': {'effective_tier': 'heavy'}}, 'git': {
            'availability': 'AVAILABLE', 'files': [
                {'path': 'a.py', 'existed': False}, {'path': 'b.py', 'existed': True},
                {'path': 'z.py', 'existed': True}]}}
        evidence['verification'] = [{'test_id': 'T', 'argv': ['python', 'a b.py', '$(oops)'],
                                     'cwd': '/repo'}]
        return change, report, evidence

    def test_machine_only_paths_commands_and_uncovered(self):
        result = build_focus(*self.fixture())
        self.assertTrue(all(i['source'] == 'machine' for i in result))
        self.assertNotIn('INJECTED PROSE', json.dumps(result))
        by_id = {i['id']: i for i in result}
        self.assertEqual([x['path'] for x in by_id['paths']['data']], ['z.py', 'b.py', 'a.py'])
        self.assertEqual(by_id['risks']['data'][0]['coverage'], 'UNCOVERED')
        self.assertEqual(shlex.split(by_id['verification']['data'][0]['command']),
                         ['python', 'a b.py', '$(oops)'])
        self.assertEqual(by_id['plan_paths']['availability'], 'NOT_AVAILABLE')

    def test_approved_exact_plan_and_explicit_risk_link(self):
        change, report, evidence = self.fixture()
        evidence['run']['planning'] = {'plan': {'approval': {'option_id': 'x'}, 'options': [
            {'id': 'x', 'touched': ['z.py'], 'creates': ['a.py']}]}}
        change['product']['assessment']['risks']['race']['criteria'] = ['AC1']
        result = {i['id']: i for i in build_focus(change, report, evidence)}
        self.assertEqual(result['plan_paths']['data']['unplanned'], ['b.py'])
        self.assertEqual(result['risks']['data'][0]['coverage'], 'EXPLICIT_CRITERION_LINK')
        self.assertEqual(result['risks']['data'][0]['tests'], ['T'])

    def test_skips_are_classified_only_from_structured_reasons(self):
        change, report, evidence = self.fixture()
        report['layers'] = {'unit': {'status': 'SKIPPED', 'reason': 'NOT_REQUIRED_BY_TIER'},
                            'smoke': {'status': 'SKIPPED', 'reason': 'NO_SYSTEM_SMOKE_SUITE'},
                            'checks': {'status': 'SKIPPED', 'reason': 'INJECTED PROSE'}}
        result = {i['id']: i for i in build_focus(change, report, evidence)}
        self.assertNotIn('INJECTED PROSE', json.dumps(result))
        gaps = {row['layer']: row for row in result['layer_gaps']['data']}
        self.assertEqual(gaps['unit']['kind'], 'TIER_SKIP')
        self.assertEqual(gaps['smoke']['kind'], 'ENVIRONMENT_GAP')
        self.assertEqual(gaps['checks']['kind'], 'UNCLASSIFIED_GAP')

    def test_file_overlap_never_covers_risk_and_prior_yes_is_retained(self):
        change, report, evidence = self.fixture()
        change['product']['assessment']['risks']['race']['evidence'] = {'locations': ['z.py:1']}
        evidence['run']['planning'] = {'plan': {'approval': {'option_id': 'x'}, 'options': [
            {'id': 'x', 'touched': ['z.py'], 'creates': [], 'risks': {'race': {'value': 'no'}}}]}}
        result = {i['id']: i for i in build_focus(change, report, evidence)}
        self.assertEqual(result['risks']['data'][0]['declared_value'], 'yes')
        self.assertEqual(result['risks']['data'][0]['coverage'], 'UNCOVERED')
