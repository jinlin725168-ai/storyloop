"""Only exact approved ordinary files cross from the disposable checkout."""

import os
import shutil
import subprocess
import unittest
from unittest import mock

from storyloop.base_agent_transfer import transfer_agent_candidate
from storyloop.models import StoryLoopError
from storyloop.base_agent_worktree import create_agent_worktree, remove_agent_worktree
from tests import test_base_agent_worktree as worktree_fixture


class BaseAgentTransferTests(unittest.TestCase):
    setUp = worktree_fixture.BaseAgentWorktreeTests.setUp
    _git = worktree_fixture.BaseAgentWorktreeTests._git

    def _checkout(self):
        checkout = create_agent_worktree(self.project, self.state)
        self.addCleanup(remove_agent_worktree, self.project, self.state)
        return checkout

    def _change(self, *paths):
        return {"change_id": self.state["change_id"], "product": {"base": {
            "scope": "foundation", "paths": list(paths)}}}

    def _transfer(self, checkout, *paths):
        with mock.patch("storyloop.base_agent_transfer.audit_base_scope"):
            return transfer_agent_candidate(self.project, self.state, self._change(*paths))

    def test_exact_binary_content_becomes_engine_candidate(self):
        checkout = self._checkout()
        content = b"\x00\xff\x0d\x0a"
        (checkout / "tracked.txt").write_bytes(content)
        committed = self._transfer(checkout, "tracked.txt")
        self.assertEqual((self.project / "tracked.txt").read_bytes(), content)
        self.assertEqual(self._git("rev-parse", "HEAD").strip(), committed)
        self.assertEqual(self._git("show", "-s", "--format=%an", "HEAD").strip(), "StoryLoop Agent")

    def test_autocrlf_checkout_commits_normalized_git_blob(self):
        self._git("config", "core.autocrlf", "true")
        checkout = self._checkout()
        (checkout / "tracked.txt").write_bytes(b"updated\r\n")
        self._transfer(checkout, "tracked.txt")
        self.assertEqual((self.project / "tracked.txt").read_bytes(), b"updated\r\n")
        self.assertEqual(self._git("show", "HEAD:tracked.txt"), "updated\n")

    def test_unapproved_path_never_touches_main(self):
        checkout = self._checkout()
        (checkout / "unknown.txt").write_text("agent\n", encoding="utf-8")
        with self.assertRaisesRegex(StoryLoopError, "outside approved"):
            self._transfer(checkout, "tracked.txt")
        self.assertFalse((self.project / "unknown.txt").exists())
        self.assertEqual(self._git("rev-parse", "HEAD").strip(), self.commit)

    def test_rename_requires_both_approved_paths(self):
        checkout = self._checkout()
        (checkout / "tracked.txt").rename(checkout / "renamed.txt")
        with self.assertRaisesRegex(StoryLoopError, "outside approved"):
            self._transfer(checkout, "renamed.txt")
        self.assertEqual(self._git("rev-parse", "HEAD").strip(), self.commit)
        self._transfer(checkout, "tracked.txt", "renamed.txt")
        self.assertFalse((self.project / "tracked.txt").exists())
        self.assertTrue((self.project / "renamed.txt").exists())

    def test_approved_deletion_is_committed(self):
        checkout = self._checkout()
        (checkout / "tracked.txt").unlink()
        self._transfer(checkout, "tracked.txt")
        self.assertFalse((self.project / "tracked.txt").exists())

    def test_symlink_is_rejected_without_reading_target(self):
        checkout = self._checkout()
        target = self.project / "tracked.txt"
        (checkout / "tracked.txt").unlink()
        try:
            (checkout / "tracked.txt").symlink_to(target)
        except OSError:
            self.skipTest("Symlink creation needs platform privileges")
        with self.assertRaisesRegex(StoryLoopError, "link"):
            self._transfer(checkout, "tracked.txt")
        self.assertEqual((self.project / "tracked.txt").read_text(encoding="utf-8"), "original\n")

    @unittest.skipUnless(os.name == "posix", "Executable mode test needs POSIX")
    def test_executable_bit_change_is_rejected(self):
        checkout = self._checkout()
        path = checkout / "tracked.txt"
        path.chmod(path.stat().st_mode | 0o111)
        with self.assertRaisesRegex(StoryLoopError, "mode"):
            self._transfer(checkout, "tracked.txt")

    def test_dirty_main_worktree_is_preserved(self):
        checkout = self._checkout()
        (checkout / "tracked.txt").write_text("agent\n", encoding="utf-8")
        (self.project / "human.txt").write_text("human\n", encoding="utf-8")
        with self.assertRaisesRegex(StoryLoopError, "clean"):
            self._transfer(checkout, "tracked.txt")
        self.assertEqual((self.project / "human.txt").read_text(encoding="utf-8"), "human\n")
        self.assertEqual((self.project / "tracked.txt").read_text(encoding="utf-8"), "original\n")

    @unittest.skipUnless(os.name == "posix", "Git fsmonitor probe uses a POSIX shell")
    def test_modified_git_pointer_cannot_execute_agent_fsmonitor(self):
        checkout = self._checkout()
        pointer = (checkout / ".git").read_text(encoding="utf-8")
        evil = checkout / ".evil"
        shutil.copytree(self.project / ".git", evil)
        marker = self.project / "fsmonitor-ran"
        script = evil / "monitor.sh"
        script.write_text(f"#!/bin/sh\ntouch '{marker}'\n", encoding="utf-8")
        script.chmod(0o700)
        with (evil / "config").open("a", encoding="utf-8") as config:
            config.write(f"\n[core]\n\tfsmonitor = {script}\n")
        (checkout / ".git").write_text("gitdir: .evil\n", encoding="utf-8")
        self.addCleanup((checkout / ".git").write_text, pointer, encoding="utf-8")
        subprocess.run(["git", "status", "--short"], cwd=checkout, check=True,
                       capture_output=True)
        self.assertTrue(marker.exists(), "The test Git config did not trigger fsmonitor")
        marker.unlink()
        (checkout / "tracked.txt").write_text("agent\n", encoding="utf-8")
        with self.assertRaises(StoryLoopError):
            self._transfer(checkout, "tracked.txt")
        self.assertFalse(marker.exists(), "Engine executed Git config from the agent checkout")

    def test_tracked_executable_keeps_git_mode_after_transfer(self):
        script = self.project / "build.sh"
        script.write_text("#!/bin/sh\necho old\n", encoding="utf-8")
        self._git("add", "build.sh")
        self._git("update-index", "--chmod=+x", "build.sh")
        self._git("commit", "-qm", "executable contract")
        self.commit = self._git("rev-parse", "HEAD").strip()
        self.state["contract_commit"] = self.commit
        self._git("config", "core.filemode", "false")
        checkout = self._checkout()
        (checkout / "build.sh").write_text("#!/bin/sh\necho new\n", encoding="utf-8")
        if os.name == "posix":
            (checkout / "build.sh").chmod(0o644)
        self._transfer(checkout, "build.sh")
        mode = self._git("ls-tree", "HEAD", "build.sh").split()[0]
        self.assertEqual(mode, "100755")

    @unittest.skipUnless(os.name == "posix", "Git filter probe uses a POSIX shell")
    def test_agent_attributes_cannot_run_configured_clean_filter(self):
        marker = self.root / "filter-ran"
        script = self.root / "filter.sh"
        script.write_text(f"#!/bin/sh\ntouch '{marker}'\ncat\n", encoding="utf-8")
        script.chmod(0o700)
        self._git("config", "filter.storyloopprobe.clean", str(script))
        checkout = self._checkout()
        (checkout / ".gitattributes").write_text("tracked.txt filter=storyloopprobe\n",
                                                encoding="utf-8")
        (checkout / "tracked.txt").write_text("changed\n", encoding="utf-8")
        subprocess.run(["git", "diff", "--name-only"], cwd=checkout, check=True,
                       capture_output=True)
        self.assertTrue(marker.exists(), "The test attribute did not invoke the filter")
        marker.unlink()
        with self.assertRaises(StoryLoopError):
            self._transfer(checkout, "tracked.txt")
        self.assertFalse(marker.exists(), "Engine ran a filter selected by agent attributes")

    def test_tracked_batch_file_uses_git_mode_not_os_extension(self):
        batch = self.project / "build.cmd"
        batch.write_text("echo old\n", encoding="utf-8")
        self._git("add", "build.cmd")
        self._git("commit", "-qm", "batch contract")
        self.commit = self._git("rev-parse", "HEAD").strip()
        self.state["contract_commit"] = self.commit
        checkout = self._checkout()
        (checkout / "build.cmd").write_text("echo new\n", encoding="utf-8")
        self._transfer(checkout, "build.cmd")
        self.assertEqual(self._git("ls-tree", "HEAD", "build.cmd").split()[0], "100644")
