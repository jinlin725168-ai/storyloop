import json
import subprocess
import sys
from unittest.mock import patch

import unittest
from tests import test_report_evidence as fixtures
from tests.test_report_evidence import git, write_json
from tests.test_report_rendering import _report
from storyloop.report_evidence import collect_evidence
from storyloop.report_presentation import build_presentation
from storyloop.report_markdown import render_presentation
from storyloop.report_reconciliation import reconcile
from storyloop.telemetry import summarize
from storyloop.smoke_runner import _run_test


class FinalFixTests(unittest.TestCase):
    setUp = fixtures.ReportEvidenceTests.setUp
    tearDown = fixtures.ReportEvidenceTests.tearDown
    _run = fixtures.ReportEvidenceTests._run
    _report = fixtures.ReportEvidenceTests._report
    _ledger = fixtures.ReportEvidenceTests._ledger
    _meta = fixtures.ReportEvidenceTests._meta
    def test_i1_unknown_and_truncated_reader_items(self):
        report = self._report()
        report.pop('layers')
        report['presentation'] = build_presentation(self.root, self.change, report)
        text = render_presentation(report)
        block = text.split('### 计划偏差')[1].split('###')[0]
        self.assertIn('APPROVED_PLAN_OR_GIT_UNAVAILABLE', block)
        self.assertNotIn('未计划变更：无', block)
        self.assertNotIn('未发现', text.split('### 验证缺口')[1].split('###')[0])
        report = _report()
        for section in ('1', '2'):
            for item in report['presentation']['sections'][section]['items']:
                item['reason'] = 'SNAPSHOT_TRUNCATED'
        text = render_presentation(report)
        for title in ('完成情况', '门禁', '验收追溯', '消费者范围', '变更路径', '计划偏差', '风险覆盖', '验证缺口', '可复制验证命令'):
            self.assertIn('SNAPSHOT_TRUNCATED', text.split('### ' + title)[1].split('###')[0])

    def test_i2_git_paths_preserve_nul_separated_names(self):
        names = ['中文.py', 'space name.py', 'tab\tname.py', 'line\nname.py']
        for name in names:
            (self.root / name).write_text('VALUE = 1\n')
        (self.root / 'tracked.py').write_text('UNIQUE_RENAME = 37\n')
        git(self.root, 'add', '.')
        git(self.root, 'commit', '-qm', 'names')
        self.base = git(self.root, 'rev-parse', 'HEAD')
        for name in names:
            (self.root / name).write_text('VALUE   =   1\n')
        git(self.root, 'mv', 'tracked.py', 'renamed 中文.py')
        (self.root / 'copy 中文.py').write_text('UNIQUE_RENAME = 37\n')
        git(self.root, 'add', '.')
        git(self.root, 'commit', '-qm', 'modify')
        self._run()
        evidence = collect_evidence(self.root, self.change, self._report())
        files = {f['path']: f for f in evidence['git']['files']}
        for name in names:
            self.assertTrue(files[name]['existed'])
            self.assertTrue(files[name]['whitespace_only'])
        self.assertEqual(files['renamed 中文.py']['previous_path'], 'tracked.py')
        self.assertEqual(files['copy 中文.py']['previous_path'], 'tracked.py')
        from storyloop.report_focus import build_focus
        evidence['run']['planning'] = {'plan': {'approval': {'option_id': 'p'}, 'options': [
            {'id': 'p', 'touched': names, 'creates': ['renamed 中文.py', 'copy 中文.py']}]}}
        plan = next(i for i in build_focus(self.change, self._report(), evidence) if i['id'] == 'plan_paths')
        self.assertEqual(plan['data']['unplanned'], [])
        self.assertEqual(plan['data']['not_changed'], [])

    def test_i2_output_budget_is_visible(self):
        self._run()
        (self.root / 'new').write_text('new')
        git(self.root, 'add', 'new')
        git(self.root, 'commit', '-qm', 'new')
        with patch('storyloop.report_evidence.MAX_GIT_BYTES', 2):
            evidence = collect_evidence(self.root, self.change, self._report())
        self.assertTrue(evidence['git']['truncated'])
        self.assertNotEqual(reconcile(self.root, self.change, self._report(), evidence)['status'], 'MATCH')

    def test_i3_empty_and_mixed_legacy_keeps_actual_anomalies(self):
        self._run()
        self._meta('orphan', 'review', status='RUNNING')
        for rows in ([], [{'role': 'old', 'wall_ms': 1}, {'role': 'review', 'call': 'new', 'wall_ms': 1}]):
            self._ledger(rows)
            evidence = collect_evidence(self.root, self.change, self._report())
            result = reconcile(self.root, self.change, self._report(), evidence)
            self.assertEqual(result['status'], 'MISMATCH')
            checks = {c['id']: c for c in result['checks']}
            self.assertEqual(checks['orphan_call_meta']['status'], 'MISMATCH')
            self.assertEqual(checks['call_lifecycle']['status'], 'MISMATCH')

    def test_i4_malformed_identifiers_and_meta_are_diagnostics(self):
        self._run()
        for value in (['bad'], {'bad': 1}, 7):
            for field in ('call', 'role'):
                self._ledger([{'role': 'review', 'call': 'good', 'wall_ms': 1},
                              {'role': 'review', 'call': 'bad', 'wall_ms': 1, field: value}])
                self._meta('good', 'review', status=['bad'])
                presentation = build_presentation(self.root, self.change, self._report())
                self.assertEqual(presentation['reconciliation']['status'], 'MISMATCH')
                evidence = collect_evidence(self.root, self.change, self._report())
                self.assertIn('good', [r.get('call') for r in evidence['entries']['ledger']])

    def test_i5_executed_python_command_is_copyable(self):
        (self.root / 'check space.py').write_text('print("OK")\n')
        test = {'id': 'T1', 'criteria': ['AC1'], 'command': ['{python}', 'check space.py'], 'timeout_seconds': 5}
        result = _run_test(self.root, test)
        path = self.root / '.storyloop/evidence/FEAT-1/acceptance-result.json'
        write_json(path, {'status': 'PASSED', 'results': [result]})
        report = self._report(traceability=[{'criterion': {'id': 'AC1'}, 'tests': [test]}])
        report['layers'] = {**report['layers'], 'acceptance': {'status': 'PASSED', 'results': [result], 'evidence_path': str(path)}}
        presentation = build_presentation(self.root, self.change, report)
        commands = next(i for i in presentation['sections']['2']['items'] if i['id'] == 'verification')['data']
        self.assertEqual(commands[0]['argv'][0], sys.executable)
        self.assertEqual(commands[0]['cwd'], str(self.root))
        executed = subprocess.run(commands[0]['argv'], cwd=commands[0]['cwd'], capture_output=True, check=True)
        self.assertIn(b'OK', executed.stdout)

    def test_i6_invalid_historical_numbers_are_unknown(self):
        for invalid in (True, -1, float('nan'), float('inf'), '1.5'):
            rows = [{'role': 'review', 'wall_ms': 1, **{key: invalid for key in ('cost_usd', 'turns', 'input_tokens', 'output_tokens')}},
                    {'role': 'review', 'wall_ms': 0, 'cost_usd': 0}]
            self._ledger(rows)
            totals = summarize(self.root, 'FEAT-1')
            self.assertIsNone(totals['cost_usd'])
            self.assertEqual(totals['known_cost_usd'], 0)
            self.assertEqual(totals['coverage']['cost_usd'], {'known': 1, 'total': 2})
            json.dumps(collect_evidence(self.root, self.change, self._report()), allow_nan=False)
        self._ledger([{'role': 'review', 'wall_ms': 1, 'cost_usd': 1e308}] * 2)
        totals = summarize(self.root, 'FEAT-1')
        self.assertIsNone(totals['cost_usd'])
        self.assertIsNone(totals['known_cost_usd'])
        json.dumps(totals, allow_nan=False)

    def test_minor_renderer_does_not_read_files_or_clock(self):
        report = _report()
        expected = render_presentation(report)
        with patch('builtins.open', side_effect=AssertionError('filesystem')), patch('pathlib.Path.read_text', side_effect=AssertionError('filesystem')), patch('time.time', side_effect=AssertionError('clock')):
            self.assertEqual(render_presentation(report), expected)

    def test_i1_builder_marks_real_large_ac_snapshot(self):
        report = self._report(traceability=[{'criterion': {'id': f'AC{i}'}, 'tests': []}
                                           for i in range(201)])
        report['presentation'] = build_presentation(self.root, self.change, report)
        text = render_presentation(report)
        self.assertIn('SNAPSHOT_TRUNCATED', text.split('### 验收追溯')[1].split('###')[0])

    def test_i3_pure_legacy_remains_unknown_and_mixed_duplicates_detected(self):
        self._run()
        self._ledger([{'role': 'legacy', 'wall_ms': 0}])
        evidence = collect_evidence(self.root, self.change, self._report())
        self.assertEqual(reconcile(self.root, self.change, self._report(), evidence)['status'], 'NOT_AVAILABLE')
        self._ledger([{'role': 'legacy', 'wall_ms': 0}] + [{'role': 'review', 'call': 'same', 'wall_ms': 0}] * 2)
        self._meta('same', 'review')
        checks = reconcile(self.root, self.change, self._report(),
                           collect_evidence(self.root, self.change, self._report()))['checks']
        self.assertEqual(next(c for c in checks if c['id'] == 'duplicate_calls')['status'], 'MISMATCH')

    def test_i4_malformed_owned_and_linked_meta_cannot_match(self):
        self._run()
        self._ledger([{'role': 'review', 'call': 'good', 'wall_ms': 0}])
        for field in ('call', 'role', 'ledger', 'status'):
            value = {'call': 'good', 'role': 'review', 'ledger': 'FEAT-1', 'status': 'OK', field: ['bad']}
            write_json(self.root / '.storyloop/state/telemetry/calls/good/meta.json', value)
            result = build_presentation(self.root, self.change, self._report())
            self.assertEqual(result['reconciliation']['status'], 'MISMATCH')

    def test_i5_candidate_absolute_paths_are_explicitly_projected(self):
        from storyloop.report_commands import execution_commands
        path = self.root / '.storyloop/evidence/FEAT-1/acceptance-result.json'
        (self.root / 'script space.py').write_text('print("replayed")')
        argv = [sys.executable, '/removed/candidate/script space.py']
        write_json(path, {'results': [{'id': 'T', 'status': 'PASS', 'command': argv, 'cwd': '/removed/candidate'}]})
        from storyloop.report_evidence import _read_object
        commands = execution_commands(self.root, 'FEAT-1', _read_object, 100000)
        self.assertEqual(commands[0]['executed_argv'], argv)
        self.assertEqual(commands[0]['argv_projection'], 'CANDIDATE_ROOT_TO_PROJECT')
        result = subprocess.run(commands[0]['argv'], cwd=commands[0]['cwd'], capture_output=True, check=True)
        self.assertIn(b'replayed', result.stdout)
        argv[-1] = '--file=/removed/candidate/script space.py'
        write_json(path, {'results': [{'id': 'T', 'status': 'PASS', 'command': argv, 'cwd': '/removed/candidate'}]})
        self.assertIsNone(execution_commands(self.root, 'FEAT-1', _read_object, 100000))

    def test_i6_every_numeric_dimension_handles_invalid_and_overflow(self):
        from storyloop.telemetry_summary import NUMERIC
        for invalid in (False, -1, float('nan'), float('inf'), '12', 10 ** 400):
            self._ledger([{'role': 'review', **{key: invalid for key in NUMERIC}}])
            totals = summarize(self.root, 'FEAT-1')
            json.dumps(totals, allow_nan=False)
            for key in NUMERIC:
                self.assertIsNone(totals[key])
            json.dumps(collect_evidence(self.root, self.change, self._report()), allow_nan=False)

    def test_i2_capture_budget_and_expired_deadline(self):
        import time
        from storyloop.report_git import _git
        result = _git(self.root, time.monotonic() + 2, 2, 'show', 'HEAD:tracked.py')
        self.assertTrue(result['limited'])
        self.assertLessEqual(len(result['stdout'].encode()), 2)
        result = _git(self.root, time.monotonic() - 1, 2, 'status')
        self.assertTrue(result['limited'])
        self.assertIsNone(result['returncode'])
