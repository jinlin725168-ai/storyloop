"""The disposable agent checkout is anchored before creation and cleaned by identity."""

from pathlib import Path
import os
import shutil
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
        def fail_add(project, *args, **kwargs):
            if args[:2] == ("worktree", "add"):
                raise StoryLoopError("add failed")
            return real_git(project, *args, **kwargs)

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

    @unittest.skipUnless(os.name == "nt", "MAX_PATH preflight applies on Windows")
    def test_long_external_temp_path_stops_before_saving_anchor(self):
        long_root = self.root / ("long" * 50)
        with mock.patch("storyloop.base_agent_worktree._safe_temp_root", return_value=long_root):
            with self.assertRaisesRegex(StoryLoopError, "path.*long"):
                create_agent_worktree(self.project, self.state)
        self.assertNotIn("agent_worktree", self.state)

    @unittest.skipUnless(os.name == "nt", "MAX_PATH preflight applies on Windows")
    def test_long_tracked_path_stops_before_saving_anchor(self):
        deep = self.project / ("a" * 70) / ("b" * 70)
        deep.mkdir(parents=True)
        (deep / "file.txt").write_text("tracked\n", encoding="utf-8")
        self._git("add", ".")
        self._git("commit", "-qm", "long tracked path")
        self.commit = self._git("rev-parse", "HEAD").strip()
        self.state["contract_commit"] = self.commit
        long_temp_root = self.root / ("x" * 80)
        long_temp_root.mkdir()
        try:
            with mock.patch("storyloop.base_agent_worktree.tempfile.gettempdir",
                            return_value=str(long_temp_root)):
                with self.assertRaisesRegex(StoryLoopError, "path.*long"):
                    create_agent_worktree(self.project, self.state)
        finally:
            remove_agent_worktree(self.project, self.state)
        self.assertNotIn("agent_worktree", self.state)

    def test_worktree_lifecycle_uses_extended_git_timeout(self):
        calls = []

        def record(project, *args, **kwargs):
            if args[:2] in {("worktree", "add"), ("worktree", "remove")}:
                calls.append((args[:2], kwargs.get("timeout_seconds", 30)))
            return real_git(project, *args, **kwargs)

        with mock.patch("storyloop.base_agent_worktree.git", side_effect=record):
            create_agent_worktree(self.project, self.state)
            remove_agent_worktree(self.project, self.state)
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(timeout >= 180 for _, timeout in calls))

    def test_symlinked_system_temp_root_is_canonicalized(self):
        actual = self.root / "external"
        actual.mkdir()
        shortcut = self.root / "shortcut"
        try:
            shortcut.symlink_to(actual, target_is_directory=True)
        except OSError:
            self.skipTest("Directory symlinks need platform privileges")
        with mock.patch("storyloop.base_agent_worktree.tempfile.gettempdir",
                        return_value=str(shortcut)):
            checkout = create_agent_worktree(self.project, self.state)
        self.addCleanup(remove_agent_worktree, self.project, self.state)
        self.assertTrue(checkout.parents[3].samefile(actual))
        self.assertIn("agent_worktree", self.state)

    def test_temp_root_inside_project_uses_external_parent(self):
        inside = self.project / "tmp"
        inside.mkdir()
        exclude = self.project / ".git/info/exclude"
        exclude.write_text(exclude.read_text(encoding="utf-8") + "\ntmp/\n",
                           encoding="utf-8")
        with mock.patch("storyloop.base_agent_worktree.tempfile.gettempdir",
                        return_value=str(inside)):
            checkout = create_agent_worktree(self.project, self.state)
        self.addCleanup(remove_agent_worktree, self.project, self.state)
        self.assertFalse(checkout.is_relative_to(self.project))

    def test_symlinked_cleanup_parent_is_rejected_without_following(self):
        actual = self.root / "actual"
        actual.mkdir()
        (actual / "human.txt").write_text("human\n", encoding="utf-8")
        shortcut = self.root / "shortcut"
        try:
            shortcut.symlink_to(actual, target_is_directory=True)
        except OSError:
            self.skipTest("Directory symlinks need platform privileges")
        self.state["agent_worktree"] = {"path": str(shortcut / "human.txt"),
                                        "contract_commit": self.commit, "attempt_id": "fake"}
        with self.assertRaisesRegex(StoryLoopError, "symbolic link"):
            remove_agent_worktree(self.project, self.state)
        self.assertEqual((actual / "human.txt").read_text(encoding="utf-8"), "human\n")

    def test_remove_failure_keeps_anchor_and_reports_manual_steps(self):
        checkout = create_agent_worktree(self.project, self.state)
        def fail_remove(project, *args, **kwargs):
            if args[:2] == ("worktree", "remove"):
                raise StoryLoopError("locked")
            return real_git(project, *args, **kwargs)

        with mock.patch("storyloop.base_agent_worktree.git", side_effect=fail_remove):
            with self.assertRaisesRegex(StoryLoopError, "worktree remove --force"):
                remove_agent_worktree(self.project, self.state)
        self.assertTrue(checkout.exists())
        self.assertIn("agent_worktree", self.state)
        remove_agent_worktree(self.project, self.state)

    def test_remove_failure_after_candidate_commit_instructs_run(self):
        checkout = create_agent_worktree(self.project, self.state)
        self.state["candidate_commit"] = self.commit

        def fail_remove(project, *args, **kwargs):
            if args[:2] == ("worktree", "remove"):
                raise StoryLoopError("locked")
            return real_git(project, *args, **kwargs)

        with mock.patch("storyloop.base_agent_worktree.git", side_effect=fail_remove):
            with self.assertRaises(StoryLoopError) as caught:
                remove_agent_worktree(self.project, self.state)
        self.assertIn("storyloop run", str(caught.exception))
        self.assertNotIn("storyloop reset", str(caught.exception))
        self.assertTrue(checkout.exists())
        remove_agent_worktree(self.project, self.state)

    def test_missing_checkout_with_stale_git_registration_is_pruned(self):
        checkout = create_agent_worktree(self.project, self.state)
        shutil.rmtree(checkout)
        self.assertIn("prunable gitdir file points to non-existent location",
                      self._git("worktree", "list", "--porcelain"))
        remove_agent_worktree(self.project, self.state)
        self.assertNotIn("agent_worktree", self.state)
        self.assertNotIn("prunable gitdir file points to non-existent location",
                         self._git("worktree", "list", "--porcelain"))

    def test_cleanup_accepts_agent_branch_switch(self):
        checkout = create_agent_worktree(self.project, self.state)
        subprocess.run(["git", "checkout", "-qb", "agent-branch"], cwd=checkout,
                       check=True, capture_output=True)
        remove_agent_worktree(self.project, self.state)
        self.assertFalse(checkout.exists())
        self.assertNotIn("agent_worktree", self.state)

    def test_missing_registration_file_reports_manual_cleanup(self):
        checkout = create_agent_worktree(self.project, self.state)
        registration = Path(self.state["agent_worktree"]["gitdir"]) / "gitdir"
        registration.unlink()
        with self.assertRaises(StoryLoopError) as caught:
            remove_agent_worktree(self.project, self.state)
        self.assertIn("worktree", str(caught.exception))
        self.assertIn("prune", str(caught.exception))
        self.assertIn("agent_worktree", self.state)
        self.assertTrue(checkout.exists())

    def test_relative_gitdir_registration_is_accepted(self):
        checkout = create_agent_worktree(self.project, self.state)
        self.addCleanup(remove_agent_worktree, self.project, self.state)
        gitdir = Path(self.state["agent_worktree"]["gitdir"])
        registration = gitdir / "gitdir"
        original = registration.read_text(encoding="utf-8")
        self.addCleanup(registration.write_text, original, encoding="utf-8")
        registration.write_text(os.path.relpath(checkout / ".git", gitdir) + "\n",
                                encoding="utf-8")
        from storyloop.base_agent_worktree import agent_git
        # Git 2.43 does not list relative registrations; 2.48 can enable them.
        with mock.patch("storyloop.base_agent_worktree._registered", return_value=True):
            self.assertEqual(agent_git(self.project, self.state, "rev-parse", "HEAD").strip(),
                             self.commit)

    @unittest.skipUnless(os.name == "posix", "POSIX paths do not have MAX_PATH")
    def test_posix_checkout_path_is_not_limited_to_240_characters(self):
        from storyloop.base_agent_worktree import _check_checkout_paths
        long_path = self.root / ("x" * 80) / ("y" * 80) / ("z" * 80)
        _check_checkout_paths(self.project, self.commit, long_path)
