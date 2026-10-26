"""Git path audits must report both ends of a rename."""

import ast
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from storyloop.models import StoryLoopError
from tests.test_baseline_behavior import git, repository


class GitDiffPathTests(unittest.TestCase):
    def test_name_only_diff_calls_disable_rename_elision(self):
        root = Path(__file__).resolve().parents[1] / "storyloop"
        offenders = []
        for path in root.glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not isinstance(node, ast.Call):
                    continue
                arguments = {arg.value for arg in node.args
                             if isinstance(arg, ast.Constant) and isinstance(arg.value, str)}
                if "diff" in arguments and "--name-only" in arguments \
                        and "--no-renames" not in arguments:
                    offenders.append(path.name)
        self.assertEqual(offenders, [])

    def test_changed_paths_reports_rename_source_and_target(self):
        from storyloop.project_git import changed_paths
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            (root / "old.md").write_text("content\n", encoding="utf-8")
            git(root, "add", "old.md")
            git(root, "commit", "-q", "-m", "old")
            source = git(root, "rev-parse", "HEAD")
            git(root, "mv", "old.md", "new.md")
            git(root, "commit", "-q", "-m", "rename")
            self.assertEqual(changed_paths(root, source, "HEAD"), {"old.md", "new.md"})

    def test_untracked_path_is_validated_after_diff_helper_refactor(self):
        from storyloop.project_boundaries import _changed_paths
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            with mock.patch("storyloop.project_boundaries._git", return_value="../escape\0"):
                with self.assertRaises(StoryLoopError):
                    _changed_paths(root)
