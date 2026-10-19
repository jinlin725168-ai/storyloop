"""Profile reports: frozen compatibility and real Git with simulated role processes."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from storyloop.report_presentation import build_presentation
from storyloop.report_markdown import render_presentation
from storyloop.storage import write_json, load_json
from storyloop.baseline import load_project_config
from storyloop.project_loop import run_change
from storyloop.telemetry import ledger_entries
from tests.routing_support import routed_case, routed_acceptance, routed_product
from tests.test_routing_planning import answer


def entry(presentation, section, name):
    return next(row for row in presentation['sections'][section]['items'] if row['id'] == name)


def finding(confidence, message='PRIVATE PROSE'):
    return {'id': 'agent-id', 'location': 'modules/orders/service.py:1', 'severity': 'P1',
            'confidence': confidence, 'introduced': True, 'target': 'implementation',
            'message': message, 'trigger': 'read', 'impact': 'wrong value', 'fix': 'correct it'}


class RoleReportTests(unittest.TestCase):
    def test_pre_change_presentation_golden_bytes(self):
        fixtures = Path(__file__).parent / 'fixtures'
        data = load_json(fixtures / 'report_presentation_v1_input.json')
        self.assertEqual(render_presentation(data).encode(),
                         (fixtures / 'report_presentation_v1_expected.md').read_bytes())

    def presentation(self, entries, disposition=None):
        evidence = {'run': {'review_disposition': disposition or {'focus': [finding(65)],
            'human': [finding(20)], 'dropped': 2}}, 'entries': {'ledger': entries},
            'availability': {'entries': 'AVAILABLE'}}
        with patch('storyloop.report_presentation.collect_evidence', return_value=evidence):
            return build_presentation(Path('/tmp'), {}, {})

    def test_three_tier_states_and_adapter_reported_protection(self):
        entries = [{'role': 'implement', 'tier_requested': 'strong', 'tier_applied': 'strong',
                    'path_guard': 'enforced', 'subagents_applied': {'mode': 'sequential'}},
                   {'role': 'review', 'tier_requested': 'strong'}, {'role': 'product'}]
        presentation = self.presentation(entries)
        tiers = entry(presentation, '5', 'role_calls')['data']
        self.assertEqual([row['status'] for row in tiers], ['已生效', '未生效', '未配置'])
        self.assertEqual([row['statistics_eligible'] for row in tiers], [True, False, False])
        markdown = render_presentation({'presentation': presentation})
        self.assertIn('适配器回报', markdown)
        self.assertIn('请求档次', markdown)
        self.assertIn('strong → strong', markdown)
        self.assertEqual(entry(presentation, '3', 'role_protection')['data'][0]['path_guard'], '生效')

    def test_reference_only_focus_and_agent_prose_in_process(self):
        presentation = self.presentation([])
        refs = entry(presentation, '2', 'review_findings')['data']
        self.assertEqual(set(refs[0]), {'id', 'location', 'severity', 'confidence'})
        self.assertNotIn('PRIVATE PROSE', json.dumps(presentation['sections']['2']))
        prose = entry(presentation, '4', 'review_disposition')
        self.assertEqual(prose['source'], 'agent_code')
        self.assertIn('PRIVATE PROSE', json.dumps(prose))
        self.assertEqual(entry(presentation, '1', 'review_human')['data'][0]['confidence'], 20)

    def test_cross_vendor_actual_missing_same_and_degraded(self):
        for vendor, degraded, expected in [('openai', None, '已生效'),
                ('anthropic', None, '跨厂商未生效'), (None, None, '跨厂商未生效'),
                ('anthropic', 'cross_vendor', '本次审查与实现同一厂商')]:
            with self.subTest(vendor=vendor, degraded=degraded):
                rows = [{'role': 'implement', 'vendor': 'anthropic', 'tier_requested': 'strong'},
                        {'role': 'review', 'vendor': vendor, 'degraded': degraded,
                         'tier_requested': 'strong'}]
                result = self.presentation(rows)
                self.assertEqual(entry(result, '1', 'cross_vendor')['data'][0]['status'], expected)


class RolesEndToEndTests(unittest.TestCase):
    def test_standard_cross_vendor_fix_loop_and_report(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'project'
            root.mkdir()
            change, _ = routed_case(root, 'standard')
            self.install(root, change['change_id'])
            result = run_change(root, change['change_id'])
            self.assertEqual(result['status'], 'PASSED', result)
            report = load_json(Path(result['report']))
            entries = ledger_entries(root, change['change_id'])
            self.assertEqual(sum(row['role'] == 'implement' for row in entries), 2)
            self.assertTrue(all(row.get('model') and row.get('vendor') and row.get('tier_applied') for row in entries))
            self.assertEqual({row['vendor'] for row in entries if row['role'] in ('implement', 'review')},
                             {'fixture-anthropic', 'fixture-openai'})
            presentation = report['presentation']
            refs = entry(presentation, '2', 'review_findings')['data']
            self.assertEqual(refs[0]['confidence'], 65)
            self.assertNotIn('PRIVATE PROSE', json.dumps(presentation['sections']['2']))
            self.assertIn('PRIVATE PROSE', json.dumps(presentation['sections']['4']))
            self.assertEqual(entry(presentation, '5', 'role_calls')['data'][-1]['status'], '已生效')

    def install(self, root, identifier):
        adapter = Path(__file__).resolve().parents[1] / 'storyloop/testing/mock_adapter.py'
        design = answer(routed_product())
        review = {'verdict': 'pass', 'summary': 'review', 'findings': [finding(65)],
                  'questions': [], 'coverage': ['implementation'],
                  'checklist': []}
        common = [{'role': 'acceptance', 'result': routed_acceptance(identifier)},
                  {'role': 'design', 'result': design},
                  {'role': 'implement', 'result': {'summary': 'write value', 'questions': []},
                   'creates': [{'path': 'modules/orders/service.py', 'content': 'VALUE = 1\n'}]}]
        reviews = [{'role': 'review', 'nth': 1, 'result': {**review, 'findings': [finding(90), finding(65)]}},
                   {'role': 'review', 'nth': 2, 'result': review}]
        config = load_project_config(root)
        config['adapters'] = {}
        for name, vendor, calls in [('writer', 'fixture-anthropic', common), ('reviewer', 'fixture-openai', reviews)]:
            directory = root.parent / name
            directory.mkdir()
            script = directory / 'script.json'
            for call in calls:
                call['telemetry'] = {'model': 'fixture-model', 'vendor': vendor, 'tier_applied': 'strong'}
            write_json(script, {'capabilities': {'vendor': vendor, 'tiers': ['strong', 'standard'],
                'subagents': False, 'path_guard': False, 'fast_checks': False, 'modes': ['*']}, 'calls': calls})
            config['adapters'][name] = {'kind': 'command', 'agent_command': [sys.executable, str(adapter), '--script', str(script)]}
        config.update(roles={'default_adapter': 'writer', 'review': {'adapter': 'reviewer'}},
                      cross_vendor_review={'enabled': True, 'on_unavailable': 'fail_closed'})
        write_json(root / '.storyloop/config.json', config)

class RoleEvidenceTests(unittest.TestCase):
    def test_frozen_role_fields_and_downgrade_reason_are_retained(self):
        from storyloop.report_evidence import collect_evidence
        from storyloop.telemetry import record_call
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_json(root / '.storyloop/state/runs/C1.json',
                       {'change_id': 'C1', 'review_disposition': {'focus': [finding(65)]}})
            write_json(root / '.storyloop/config.json', {'roles': {'review': {
                'tier': 'standard', 'reason': 'bounded demo token=do-not-leak'}}})
            record_call(root, 'review', 'C1', {'model': 'fixture', 'vendor': 'fixture',
                'tier_applied': 'standard', 'path_guard': 'unsupported',
                'subagents_applied': {'mode': 'sequential'}}, 1,
                engine_fields={'tier_requested': 'standard', 'adapter': 'fixture', 'degraded': 'cross_vendor'})
            evidence = collect_evidence(root, {'change_id': 'C1'}, {})
            self.assertEqual(evidence['run']['review_disposition']['focus'][0]['confidence'], 65)
            self.assertEqual(evidence['entries']['ledger'][0]['tier_requested'], 'standard')
            self.assertEqual(evidence['entries']['ledger'][0]['degraded'], 'cross_vendor')
            self.assertTrue(evidence['role_downgrades'])
            self.assertNotIn('do-not-leak', json.dumps(evidence))

class RoleReportMalformedTests(unittest.TestCase):
    def test_malformed_adapter_diagnostics_stay_unknown(self):
        harness = RoleReportTests()
        rows = [{'role': 'implement', 'tier_requested': 'strong', 'vendor': {'bad': True}},
                {'role': 'review', 'tier_requested': 'strong', 'vendor': 'openai',
                 'path_guard': [], 'subagents_applied': ['invalid']}]
        presentation = harness.presentation(rows)
        self.assertEqual(entry(presentation, '1', 'cross_vendor')['data'][0]['status'], '跨厂商未生效')
        self.assertEqual(entry(presentation, '3', 'role_protection')['data'][1]['path_guard'], '未生效')
