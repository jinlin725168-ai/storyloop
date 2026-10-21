"""Behavioral regressions requested in PR 15 review."""
from datetime import date
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from storyloop import ci_results, metrics_collect
from storyloop.metrics_compute import contract_ratio, lessons, local_green_ci_red
from storyloop.metrics_compute_support import _numstat
from tests.lesson_support import git_repo, cleanup_repo, commit_file, head
from tests.metrics_support import write_change, write_run, write_ledger
from tests.test_metrics_compute import rec


class MetricsReviewTests(unittest.TestCase):
    def test_binary_numstat_keeps_text_totals(self):
        self.assertEqual(_numstat('2\t3\tapp.py\n-\t-\tasset.bin\n'), 5)
        self.assertEqual(_numstat('-\t-\tasset.bin\n'), 0)
        self.assertIsNone(_numstat('bad\t2\tapp.py\n'))

    def test_mixed_binary_git_diff_keeps_contract_ratio(self):
        root = git_repo()
        self.addCleanup(cleanup_repo, root)
        source = head(root)
        contract = commit_file(root, 'contract.md', 'contract\n')
        (root / 'asset.bin').write_bytes(b'\x00binary')
        implementation = commit_file(root, 'app.py', 'one\ntwo\n')
        result = contract_ratio(root, [rec('A', commits={
            'source_head': source, 'contract_commit': contract,
            'implementation_commit': implementation})])
        self.assertEqual((result['value'], result['n']), (0.5, 1))

    def test_ci_error_layers_count_as_failures(self):
        result = local_green_ci_red({'A': {'status': 'FAILED', 'layers': {
            'acceptance': 'ERROR', 'smoke': 'FAILED'}},
            'B': {'status': 'FAILED', 'layers': {'acceptance': 'PASSED', 'smoke': 'ERROR'}}},
            [rec('A'), rec('B')])
        self.assertEqual(result['by'], {'smoke': 2, 'acceptance': 1})
        self.assertEqual(result['value'], 1.0)

    def test_ci_ancestry_honors_git_timeout_and_containment(self):
        root = git_repo()
        self.addCleanup(cleanup_repo, root)
        commit = head(root)
        for result in ({'contained': False}, {'contained': True, 'timed_out': True}):
            with self.subTest(result=result), patch('storyloop.baseline.run_process', return_value=result):
                self.assertIsNone(ci_results._related(root, commit, commit))

    def test_since_reports_changes_without_a_filterable_date(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            write_change(root, 'UNDATED', approved_at=None)
            write_run(root, 'UNDATED', started_at=None)
            write_change(root, 'OLD', approved_at='2020-01-01T00:00:00+00:00')
            result = metrics_collect.collect(root, date(2026, 1, 1))
            self.assertEqual(result['changes'], [])
            self.assertEqual(result['missing'], [{'change_id': 'UNDATED', 'reason': 'UNREADABLE'}])
            self.assertEqual(len(metrics_collect.collect(root)['changes']), 2)

    def test_ledger_uses_only_the_validated_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            write_ledger(root, 'A', [{'role': 'implement', 'cost_usd': 1}])
            read = metrics_collect._read_object
            def replace_after_read(*args):
                value = read(*args)
                write_ledger(root, 'A', [{'role': 'review', 'cost_usd': 999}])
                return value
            with patch('storyloop.metrics_collect._read_object', side_effect=replace_after_read):
                self.assertEqual(metrics_collect._ledger(root, 'A'),
                                 [{'role': 'implement', 'cost_usd': 1}])

    def test_ci_without_merge_has_an_explicit_reason(self):
        from tests.test_ci_results import _write_ci
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            _write_ci(root, 'ci-a.json', change='A', status='PASSED',
                      commit='a' * 40, at='2026-10-20T00:00:00+00:00')
            values, missing = ci_results.load_ci_results(root, root, [rec('A')])
            self.assertEqual(values, {})
            self.assertEqual(missing, [{'change_id': 'A', 'reason': 'CI_MERGE_COMMIT_ABSENT'}])

    def test_m6_reports_locally_passed_changes_without_ci(self):
        for results in ({}, None):
            with self.subTest(results=results):
                value = local_green_ci_red(results, [rec('A'), rec('B', status='FAILED')])
                self.assertEqual(value['missing'], [
                    {'change_id': 'A', 'reason': 'CI_RESULT_ABSENT'}])
                self.assertEqual(value['n'], 0)

    def test_absent_ledger_is_empty_but_corrupt_ledger_is_unknown(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            (root / 'openspec/lessons').mkdir(parents=True)
            write_change(root, 'A')
            write_change(root, 'B')
            write_ledger(root, 'A', [{'lessons_provided': ['L-1'], 'lessons_claimed': []}])
            collected = metrics_collect.collect(root)
            result = lessons(root, collected['changes'])['recurrence_rate']
            self.assertEqual((result['value'], result['n'], result['missing']), (0, 1, []))
            self.assertEqual(metrics_collect._ledger(root, 'B'), [])
            (root / '.storyloop/state/telemetry/B.json').write_text('{broken', encoding='utf-8')
            collected = metrics_collect.collect(root)
            result = lessons(root, collected['changes'])['recurrence_rate']
            self.assertIsNone(result['value'])
            self.assertIn({'change_id': 'B', 'reason': 'UNREADABLE'}, result['missing'])
