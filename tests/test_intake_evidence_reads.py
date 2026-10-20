"""Evidence locations are checked against one commit: verify it once, read each path once."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from storyloop import intake
from storyloop.models import StoryLoopError
from tests.routing_support import readiness_repository
from tests.test_baseline_behavior import git


def _assessment(locations):
    evidence = {'locations': locations, 'search_scope': ['app']}
    return {'risks': {f'r{i}': {'value': 'no', 'evidence': evidence} for i in range(8)},
            'criteria': [{'criterion_id': 'AC-1', 'evidence': evidence}]}


class EvidenceReadTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        readiness_repository(self.root)
        self.commit = git(self.root, 'rev-parse', 'HEAD').strip()

    def _git_calls(self, assessment):
        calls = []
        real = intake._git

        def counted(*args, **kwargs):
            calls.append(args[1:])
            return real(*args, **kwargs)

        with patch('storyloop.intake._git', side_effect=counted):
            intake._validate_locations(self.root, self.commit, assessment)
        return calls

    def test_repeated_locations_verify_the_commit_once_and_read_each_path_once(self):
        # 9 judgments x 2 locations in the same file: previously 3 git processes per location.
        calls = self._git_calls(_assessment(['app/service.py:1', 'app/service.py:1']))
        self.assertEqual([call[0] for call in calls], ['cat-file', 'ls-tree', 'show'])

    def test_each_distinct_path_is_still_checked(self):
        (self.root / 'app/extra.py').write_text('X = 1\n', encoding='utf-8')
        git(self.root, 'add', 'app/extra.py')
        git(self.root, 'commit', '-qm', 'extra')
        self.commit = git(self.root, 'rev-parse', 'HEAD').strip()
        calls = self._git_calls(_assessment(['app/service.py:1', 'app/extra.py:1']))
        self.assertEqual(sorted(call[-1] for call in calls if call[0] == 'show'),
                         [f'{self.commit}:app/extra.py', f'{self.commit}:app/service.py'])

    def test_line_beyond_the_file_is_still_rejected(self):
        with self.assertRaisesRegex(StoryLoopError, 'Evidence line absent'):
            intake._validate_locations(self.root, self.commit, _assessment(['app/service.py:9999']))

    def test_missing_path_is_still_rejected(self):
        with self.assertRaisesRegex(StoryLoopError, 'Evidence path absent'):
            intake._validate_locations(self.root, self.commit, _assessment(['app/missing.py:1']))


if __name__ == '__main__':
    unittest.main()
