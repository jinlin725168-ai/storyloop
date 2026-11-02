"""The committed-tree baseline must not depend on how the temporary folder is spelled."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from storyloop.characterization_baseline import _baseline


def _git(root: Path, *args: str) -> str:
    return subprocess.run(['git', *args], cwd=root, capture_output=True, text=True, check=True).stdout


class BaselinePathSpellingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name) / 'project'
        (self.project / 'app').mkdir(parents=True)
        (self.project / 'app' / 'service.py').write_text('VALUE = 1\n', encoding='utf-8')
        _git(self.project, 'init', '-q')
        _git(self.project, 'add', '.')
        _git(self.project, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '-m', 'base')
        self.head = _git(self.project, 'rev-parse', 'HEAD').strip()

    @unittest.skipUnless(os.name == 'posix', 'symlinks need privileges on Windows')
    def test_candidate_reached_through_a_symlink(self):
        # Same shape as a Windows 8.3 short temp path: the spelling differs from the resolved path.
        real = Path(self.temp.name) / 'real'
        real.mkdir()
        alias = Path(self.temp.name) / 'alias'
        alias.symlink_to(real, target_is_directory=True)

        self.assertEqual(_baseline(self.project, alias, self.head), {'app/service.py'})
        self.assertEqual((real / 'app' / 'service.py').read_text(encoding='utf-8'), 'VALUE = 1\n')

    def test_candidate_in_the_system_temp_folder(self):
        # On Windows runners the temp folder is spelled with a short name (RUNNER~1).
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(_baseline(self.project, Path(folder), self.head), {'app/service.py'})


if __name__ == '__main__':
    unittest.main()
