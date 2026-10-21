"""Initialization must reject unsupported external Git control before any writes."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from storyloop.baseline import bootstrap_project
from storyloop.cli import main
from storyloop.models import StoryLoopError
from storyloop.onboarding import scaffold_project
from tests.test_baseline_behavior import git, repository


def snapshot(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


class InitGitControlTests(unittest.TestCase):
    def assert_refused_without_writes(self, project, metadata):
        before = snapshot(metadata)
        head = git(project, 'rev-parse', 'HEAD')
        pointer = (project / '.git').read_bytes()
        for initialize in (bootstrap_project, lambda root: scaffold_project(root, ['generic'])):
            with self.subTest(initialize=initialize.__name__):
                with self.assertRaisesRegex(StoryLoopError, 'independent clone'):
                    initialize(project)
                self.assertFalse((project / '.storyloop').exists())
                self.assertFalse((project / 'acceptance').exists())
                self.assertEqual((project / '.git').read_bytes(), pointer)
                self.assertEqual(git(project, 'rev-parse', 'HEAD'), head)
                self.assertEqual(snapshot(metadata), before)

    def test_linked_worktree_rejected_without_changing_shared_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'source'
            source.mkdir()
            repository(source)
            target = root / 'linked'
            git(source, 'worktree', 'add', '-q', '-b', 'isolated', str(target))
            self.assert_refused_without_writes(target, source / '.git')

    def test_separate_git_directory_rejected_without_changing_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'source'
            source.mkdir()
            repository(source)
            metadata = root / 'metadata'
            git(source, 'init', '-q', '--separate-git-dir', str(metadata))
            self.assert_refused_without_writes(source, metadata)

    def test_cli_reports_actionable_error_for_both_init_modes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'source'
            source.mkdir()
            repository(source)
            target = root / 'linked'
            git(source, 'worktree', 'add', '-q', '-b', 'isolated', str(target))
            for extra in ([], ['--scaffold', '--stack', 'generic']):
                with self.subTest(extra=extra), contextlib.redirect_stderr(io.StringIO()) as errors:
                    with contextlib.redirect_stdout(io.StringIO()):
                        code = main(['init', str(target), '--json', *extra])
                    self.assertEqual(code, 2)
                    error = json.loads(errors.getvalue())
                    self.assertIn('independent clone', error['message'])
                    self.assertNotIn('FileExistsError', error['message'])
                    self.assertFalse((target / '.storyloop').exists())
                    self.assertFalse((target / 'acceptance').exists())


if __name__ == '__main__':
    unittest.main()
