"""The disposable agent checkout is anchored before creation and cleaned by identity."""

from pathlib import Path
import os
import subprocess
import tempfile
import unittest
from unittest import mock

from storyloop.models import StoryLoopError
from storyloop.git_command import git as real_git
from storyloop.run_state import run_path
from storyloop.storage import load_json
from storyloop.base_agent_worktree import create_agent_worktree, remove_agent_worktree


class BaseAgentWorktreeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self._git("init", "-q")
        self._git("config", "user.name", "Test")
        self._git("config", "user.email", "test@example.invalid")
        (self.project / "tracked.txt").write_text("original\n", encoding="utf-8")
        (self.project / ".gitignore").write_text(".storyloop/\n.venv/\n", encoding="utf-8")
        self._git("add", "tracked.txt", ".gitignore")
        self._git("commit", "-qm", "contract")
        self.commit = self._git("rev-parse", "HEAD").strip()
        self.state = {"change_id": "BASE-20261026-001", "contract_commit": self.commit}

    def _git(self, *args):
        return subprocess.run(["git", *args], cwd=self.project, check=True,
                              capture_output=True, text=True).stdout

    def test_checkout_is_detached_external_and_contains_only_tracked_files(self):
        (self.project / ".venv").mkdir()
        (self.project / ".venv/secret.txt").write_text("human", encoding="utf-8")
        checkout = create_agent_worktree(self.project, self.state)
        self.addCleanup(remove_agent_worktree, self.project, self.state)
        self.assertFalse(checkout.is_relative_to(self.project))
        self.assertEqual((checkout / "tracked.txt").read_text(encoding="utf-8"), "original\n")
        self.assertFalse((checkout / ".venv").exists())
        self.assertEqual(self._git("worktree", "list", "--porcelain").count("worktree "), 2)
        self.assertEqual(load_json(run_path(self.project, self.state["change_id"]))["agent_worktree"]
                         ["contract_commit"], self.commit)

    @unittest.skipUnless(os.name == "posix", "POSIX directory modes only")
    def test_every_engine_owned_parent_is_private(self):
        checkout = create_agent_worktree(self.project, self.state)
        self.addCleanup(remove_agent_worktree, self.project, self.state)
        for folder in (checkout.parents[2], checkout.parents[1], checkout.parent):
            self.assertEqual(folder.stat().st_mode & 0o777, 0o700, str(folder))

    def test_dirty_checkout_is_removed_without_touching_main(self):
        checkout = create_agent_worktree(self.project, self.state)
        (checkout / "tracked.txt").write_text("agent\n", encoding="utf-8")
        (checkout / "untracked.txt").write_text("agent\n", encoding="utf-8")
        remove_agent_worktree(self.project, self.state)
        self.assertFalse(checkout.exists())
        self.assertEqual((self.project / "tracked.txt").read_text(encoding="utf-8"), "original\n")
        self.assertFalse((self.project / "untracked.txt").exists())

    def test_existing_human_changes_stop_before_anchor(self):
        (self.project / "human.txt").write_text("human\n", encoding="utf-8")
        with self.assertRaisesRegex(StoryLoopError, "clean"):
            create_agent_worktree(self.project, self.state)
        self.assertNotIn("agent_worktree", self.state)
        self.assertTrue((self.project / "human.txt").exists())

    def test_failed_add_keeps_saved_anchor_for_restart(self):
        def fail_add(project, *args):
            if args[:2] == ("worktree", "add"):
                raise StoryLoopError("add failed")
            return real_git(project, *args)

        with mock.patch("storyloop.base_agent_worktree.git", side_effect=fail_add):
            with self.assertRaisesRegex(StoryLoopError, "add failed"):
                create_agent_worktree(self.project, self.state)
        saved = load_json(run_path(self.project, self.state["change_id"]))
        self.assertEqual(saved["agent_worktree"]["contract_commit"], self.commit)
        self.assertEqual(saved["agent_worktree"]["path"], self.state["agent_worktree"]["path"])

    def test_unknown_directory_is_preserved(self):
        unknown = self.root / "unknown"
        unknown.mkdir()
        (unknown / "human.txt").write_text("human\n", encoding="utf-8")
        self.state["agent_worktree"] = {"path": str(unknown), "contract_commit": self.commit,
                                        "attempt_id": "unknown"}
        with self.assertRaises(StoryLoopError):
            remove_agent_worktree(self.project, self.state)
        self.assertTrue((unknown / "human.txt").exists())

    def test_long_external_temp_path_stops_before_saving_anchor(self):
        long_root = self.root / ("long" * 50)
        long_root.mkdir()
        with mock.patch("storyloop.base_agent_worktree.tempfile.gettempdir",
                        return_value=str(long_root)):
            with self.assertRaisesRegex(StoryLoopError, "path.*long"):
                create_agent_worktree(self.project, self.state)
        self.assertNotIn("agent_worktree", self.state)

    def test_symlinked_temp_root_is_rejected(self):
        actual = self.root / "external"
        actual.mkdir()
        shortcut = self.root / "shortcut"
        try:
            shortcut.symlink_to(actual, target_is_directory=True)
        except OSError:
            self.skipTest("Directory symlinks need platform privileges")
        with mock.patch("storyloop.base_agent_worktree.tempfile.gettempdir",
                        return_value=str(shortcut)):
            with self.assertRaisesRegex(StoryLoopError, "symbolic link"):
                create_agent_worktree(self.project, self.state)
        self.assertNotIn("agent_worktree", self.state)

    def test_remove_failure_keeps_anchor_and_reports_manual_steps(self):
        checkout = create_agent_worktree(self.project, self.state)
        def fail_remove(project, *args):
            if args[:2] == ("worktree", "remove"):
                raise StoryLoopError("locked")
            return real_git(project, *args)

        with mock.patch("storyloop.base_agent_worktree.git", side_effect=fail_remove):
            with self.assertRaisesRegex(StoryLoopError, "worktree remove --force"):
                remove_agent_worktree(self.project, self.state)
        self.assertTrue(checkout.exists())
        self.assertIn("agent_worktree", self.state)
        remove_agent_worktree(self.project, self.state)
