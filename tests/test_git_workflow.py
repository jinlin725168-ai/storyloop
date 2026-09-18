"""Opt-in, argv-only Git discipline tests."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from storyloop.git_workflow import ensure_clean_feature_branch, merge_no_ff
from storyloop.models import StoryLoopError


def git(project: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([shutil.which("git"), *args], cwd=project,
                          capture_output=True, text=True, encoding="utf-8", check=False)


@unittest.skipUnless(shutil.which("git"), "git is unavailable")
class GitWorkflowTests(unittest.TestCase):
    def make_repo(self, folder: str) -> Path:
        project = Path(folder)
        self.assertEqual(git(project, "init", "-b", "main").returncode, 0)
        self.assertEqual(git(project, "config", "user.email", "test@example.invalid").returncode, 0)
        self.assertEqual(git(project, "config", "user.name", "StoryLoop Test").returncode, 0)
        (project / "README.md").write_text("base\n", encoding="utf-8")
        self.assertEqual(git(project, "add", "README.md").returncode, 0)
        self.assertEqual(git(project, "commit", "-m", "base").returncode, 0)
        self.assertEqual(git(project, "switch", "-c", "feature/safe-change").returncode, 0)
        return project

    def test_clean_tree_and_exact_feature_branch_are_required(self):
        with tempfile.TemporaryDirectory() as folder:
            project = self.make_repo(folder)
            result = ensure_clean_feature_branch(project, "feature/safe-change")
            self.assertEqual(result["branch"], "feature/safe-change")
            with self.assertRaisesRegex(StoryLoopError, "branch"):
                ensure_clean_feature_branch(project, "feature/other")
            (project / "dirty.txt").write_text("dirty", encoding="utf-8")
            with self.assertRaisesRegex(StoryLoopError, "clean"):
                ensure_clean_feature_branch(project, "feature/safe-change")

    def test_invalid_feature_ref_is_rejected_before_git(self):
        calls = []
        with self.assertRaises(StoryLoopError):
            ensure_clean_feature_branch(Path("."), "main;git push", runner=calls.append)
        self.assertEqual(calls, [])

    def test_merge_uses_exact_no_ff_argv_and_never_pushes(self):
        calls = []
        responses = iter([
            {"returncode": 0, "stdout": "", "stderr": ""},
            {"returncode": 0, "stdout": "main\n", "stderr": ""},
            {"returncode": 0, "stdout": "", "stderr": ""},
            {"returncode": 0, "stdout": "merged", "stderr": ""},
        ])

        def runner(argv, cwd):
            calls.append(argv)
            return next(responses)

        merge_no_ff(Path("."), "feature/safe-change", "main", runner=runner,
                    executable="git")
        self.assertEqual(
            calls[-1],
            ["git", "-c", f"core.hooksPath={os.devnull}", "merge", "--no-ff", "--no-edit",
             "--no-verify", "feature/safe-change"],
        )
        self.assertFalse(any("push" in call for call in calls))

    def test_merge_conflict_fails_closed(self):
        responses = iter([
            {"returncode": 0, "stdout": "", "stderr": ""},
            {"returncode": 0, "stdout": "main\n", "stderr": ""},
            {"returncode": 0, "stdout": "", "stderr": ""},
            {"returncode": 1, "stdout": "", "stderr": "conflict details"},
        ])
        with self.assertRaisesRegex(StoryLoopError, "conflict"):
            merge_no_ff(Path("."), "feature/safe-change", "main",
                        runner=lambda argv, cwd: next(responses), executable="git")


if __name__ == "__main__":
    unittest.main()
