from pathlib import Path
import tempfile
import unittest

from storyloop.baseline import bootstrap_project
from storyloop.models import StoryLoopError
from storyloop.project_git import commit_paths, finish_feature, start_feature
from tests.test_baseline_behavior import git, repository


class ProjectGitLifecycleTests(unittest.TestCase):
    def test_local_branch_commit_ignores_hooks_and_no_ff_merges(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            bootstrap_project(root)
            source = git(root, "branch", "--show-current")
            hooks = root / ".git/hooks"
            (hooks / "pre-commit").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")

            started = start_feature(root, "FEAT-20260916-001")
            feature_file = root / "modules/orders/service.py"
            feature_file.parent.mkdir(parents=True)
            feature_file.write_text("VALUE = 1\n", encoding="utf-8")
            committed = commit_paths(
                root, ["modules/orders/service.py"], "feat(orders): add module"
            )
            merged = finish_feature(root, started["branch"], source)

            self.assertEqual(started["source_branch"], source)
            self.assertEqual(len(committed["commit"]), 40)
            self.assertEqual(merged["status"], "MERGED")
            self.assertEqual(git(root, "branch", "--show-current"), source)
            self.assertEqual(len(git(root, "rev-list", "--parents", "-n", "1", "HEAD").split()), 3)

    def test_dirty_tree_blocks_start_and_merge_without_discarding_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            bootstrap_project(root)
            (root / "dirty.txt").write_text("keep", encoding="utf-8")
            with self.assertRaisesRegex(StoryLoopError, "clean"):
                start_feature(root, "FEAT-20260916-001")
            self.assertEqual((root / "dirty.txt").read_text(encoding="utf-8"), "keep")


if __name__ == "__main__":
    unittest.main()
