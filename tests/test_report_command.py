"""`storyloop report` points at a change's acceptance report, or says why it cannot (D1 gap).

The first coverage audit (2026-11-17) found no test of the command itself, positive or error path.
"""
from pathlib import Path
import tempfile
import unittest

from storyloop.cli import dispatch, parser
from storyloop.models import StoryLoopError
from tests.routing_support import readiness_repository


class ReportCommandTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        readiness_repository(self.root)

    def test_report_names_the_change_report_path(self):
        path = self.root / 'reports/storyloop/FEAT-1/report.md'
        path.parent.mkdir(parents=True)
        path.write_text('# Report\n', encoding='utf-8')
        result = dispatch(parser().parse_args(['report', str(self.root), '--change', 'FEAT-1']))
        self.assertEqual((result['status'], result['change_id']), ('REPORT', 'FEAT-1'))
        self.assertEqual(Path(result['report']), path)

    def test_report_without_change_or_report_says_why(self):
        with self.assertRaisesRegex(StoryLoopError, 'needs --change'):
            dispatch(parser().parse_args(['report', str(self.root)]))
        with self.assertRaisesRegex(StoryLoopError, 'not available'):
            dispatch(parser().parse_args(['report', str(self.root), '--change', 'FEAT-404']))


if __name__ == '__main__':
    unittest.main()
