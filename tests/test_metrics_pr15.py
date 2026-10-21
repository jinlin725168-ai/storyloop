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
