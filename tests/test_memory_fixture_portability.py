"""Portable memory fixtures preserve golden bytes and clean read-only Git files."""
from pathlib import Path
import errno
import os
import stat
import tempfile
import unittest
from unittest.mock import patch

from tests.lesson_support import cleanup_repo, git_repo
from tests.test_baseline_behavior import git, repository


class FixturePortabilityTests(unittest.TestCase):
    def test_report_goldens_keep_lf_under_autocrlf_checkout(self):
        source = Path(__file__).resolve().parents[1]
        fixtures = ('report_legacy_receipt.md', 'report_presentation_v1_expected.md')
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            repository(root)
            (root / '.gitattributes').write_bytes((source / '.gitattributes').read_bytes())
            expected = {}
            for name in fixtures:
                relative = 'tests/fixtures/' + name
                expected[relative] = (source / relative).read_bytes().replace(b'\r\n', b'\n')
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(expected[relative])
            git(root, 'add', '.')
            git(root, 'commit', '-qm', 'goldens')
            git(root, 'config', 'core.autocrlf', 'true')
            for relative in expected:
                (root / relative).unlink()
            git(root, 'checkout', '--', *expected)
            for relative, content in expected.items():
                with self.subTest(relative=relative):
                    self.assertEqual((root / relative).read_bytes(), content)

    def test_cleanup_retries_a_readonly_file_and_removes_repository(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'repo'
            target = root / '.git/objects/ab/readonly-object'
            target.parent.mkdir(parents=True)
            target.write_bytes(b'fixture')
            target.chmod(stat.S_IREAD)
            original = os.unlink
            failed = False
            def readonly_once(path, *args, **kwargs):
                nonlocal failed
                if Path(path).name == target.name and not failed:
                    failed = True
                    raise PermissionError(errno.EACCES, 'read-only fixture', str(path))
                return original(path, *args, **kwargs)
            with patch('os.unlink', side_effect=readonly_once):
                cleanup_repo(root)
            self.assertTrue(failed)
            self.assertFalse(root.exists())

    def test_cleanup_does_not_hide_other_filesystem_errors(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'repo'
            root.mkdir()
            target = root / 'file'
            target.write_bytes(b'fixture')
            failure = OSError(errno.EIO, 'fixture I/O failure')
            with patch('os.unlink', side_effect=failure):
                with self.assertRaises(OSError) as raised:
                    cleanup_repo(root)
            self.assertIs(raised.exception, failure)
            self.assertTrue(target.exists())

    def test_git_repository_fixture_returns_resolved_root(self):
        root = git_repo()
        self.addCleanup(cleanup_repo, root)
        self.assertEqual(root, root.resolve())
