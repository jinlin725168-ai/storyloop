import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from storyloop.report_presentation import build_presentation
from storyloop.report_evidence import collect_evidence


class PresentationTests(unittest.TestCase):
    def fixture(self):
        change = {'change_id': 'C1', 'product': {'goal': 'Goal', 'non_goals': ['No migration'],
            'stories': [{'acceptance': [{'id': 'AC1', 'kind': 'manual'}]}]}}
        report = {'status': 'PASSED', 'review': {'summary': 'AGENT OPINION'}, 'oracle': {
            'skills_used': {'old': '1'}, 'executed': {}, 'uncounted': []}, 'traceability': [
            {'criterion': {'id': 'AC1', 'kind': 'manual'}, 'tests': [
                {'id': 'human', 'note': 'checked', 'accepted_at': 'now', 'result': {'status': 'ACCEPTED'}}]}]}
        evidence = {'run': {'routing': {'effective_tier': 'heavy'}, 'facts': [
            {'statement': 'code claim', 'location': 'a.py:1', 'role': 'review', 'attempt': 1}],
            'failures': [], 'agent_failures': [], 'resets': [{'attempt': 1}]},
            'answers': [{'answer': 'human answer'}], 'entries': {'ledger': [{'call': 'X', 'skills': []}]},
            'telemetry': {'cost_usd': None, 'known_cost_usd': .2, 'coverage': {'cost_usd': {'known': 1, 'total': 2}}}}
        return change, report, evidence

    def test_seven_sections_sources_unknowns_cost_and_process(self):
        change, report, evidence = self.fixture()
        with patch('storyloop.report_presentation.collect_evidence', return_value=evidence):
            result = build_presentation(Path('/tmp'), change, report)
        self.assertEqual(set(result['sections']), set('0123456'))
        self.assertTrue(all(i['source'] == 'machine' for i in result['sections']['2']['items']))
        self.assertNotIn('AGENT OPINION', json.dumps(result['sections']['2']))
        items = {i['id']: i for s in result['sections'].values() for i in s['items']}
        self.assertEqual(items['ci']['availability'], 'NOT_AVAILABLE')
        self.assertEqual(items['memory']['reason'], 'MEMORY_MODULE_NOT_IMPLEMENTED')
        self.assertIsNone(items['cost']['data']['cost_usd'])
        self.assertEqual(items['cost']['data']['known_cost_usd'], .2)
        self.assertEqual(items['facts:0']['source'], 'agent_code')
        self.assertEqual(items['review']['source'], 'agent_sample')
        self.assertEqual(items['answers']['source'], 'human')
        self.assertEqual(items['manual_confirmations']['source'], 'human')
        self.assertEqual(items['skills_legacy']['reason'], 'STALE_POSSIBLE')
        self.assertIsNone(items['discovered']['data'])
        self.assertEqual(items['completion']['data'], {'completed': 1, 'total': 1})
        self.assertEqual(items['failures']['data'], [])
        self.assertEqual(items['resets']['data'], [{'attempt': 1}])

    def test_light_has_same_sections_and_missing_is_not_empty(self):
        with patch('storyloop.report_presentation.collect_evidence', return_value={}):
            result = build_presentation(Path('/tmp'), {'intake': {'tier': 'light'}}, {})
        self.assertEqual(set(result['sections']), set('0123456'))
        items = {i['id']: i for s in result['sections'].values() for i in s['items']}
        self.assertIsNone(items['failures']['data'])
        self.assertEqual(items['failures']['availability'], 'NOT_AVAILABLE')

    def test_answers_are_fixed_path_bounded_redacted_and_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / '.storyloop/state/answers/C1.json'
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({'version': 1, 'change_id': 'C1', 'entries': [
                {'answer': 'token=secret-value', 'question': 'why', 'unknown': 'excluded'}]}))
            evidence = collect_evidence(root, {'change_id': 'C1'}, {})
            self.assertEqual(evidence['availability']['answers'], 'AVAILABLE')
            self.assertNotIn('secret-value', json.dumps(evidence['answers']))
            self.assertNotIn('unknown', evidence['answers'][0])
            path.unlink()
            path.symlink_to(root / 'elsewhere.json')
            self.assertIsNone(collect_evidence(root, {'change_id': 'C1'}, {})['answers'])
            self.assertIsNone(collect_evidence(root, {'change_id': '../outside'}, {})['answers'])

    def test_unavailable_ledger_and_missing_process_remain_unknown(self):
        change, report, evidence = self.fixture()
        evidence['entries'] = {'ledger': []}
        evidence['availability'] = {'entries': 'NOT_AVAILABLE'}
        evidence['run'] = {}
        with patch('storyloop.report_presentation.collect_evidence', return_value=evidence):
            result = build_presentation(Path('/tmp'), change, report)
        items = {i['id']: i for s in result['sections'].values() for i in s['items']}
        self.assertIsNone(items['skills']['data'])
        self.assertIsNone(items['agent_failures']['data'])
        self.assertEqual(items['merge_commit']['availability'], 'PENDING')

    def test_answer_limits_and_cross_root_parent_symlink(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            root = Path(directory)
            path = root / '.storyloop/state/answers/C1.json'
            path.parent.mkdir(parents=True)
            value = {'version': 1, 'change_id': 'C1', 'entries': [{'answer': 'a'}] * 201}
            path.write_text(json.dumps(value))
            evidence = collect_evidence(root, {'change_id': 'C1'}, {})
            self.assertEqual(len(evidence['answers']), 200)
            self.assertTrue(evidence['availability']['answers_truncated'])
            path.write_text(' ' * 2_000_001)
            self.assertIsNone(collect_evidence(root, {'change_id': 'C1'}, {})['answers'])
            path.unlink()
            path.parent.rmdir()
            path.parent.symlink_to(outside, target_is_directory=True)
            (Path(outside) / 'C1.json').write_text(json.dumps(value))
            self.assertIsNone(collect_evidence(root, {'change_id': 'C1'}, {})['answers'])
