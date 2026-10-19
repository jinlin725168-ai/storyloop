"""A human can reset a stuck run so it continues, with the reason recorded."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from storyloop.models import StoryLoopError
from storyloop.project_loop import load_run_state, run_change
from storyloop.run_recovery import reset_run
from tests.test_baseline_behavior import git
from tests.test_workspace_scratch import _approved as scratch_approved, _services
from tests.routing_support import legacy_create_change
from unittest.mock import patch

NOW = datetime(2026, 9, 19, tzinfo=timezone.utc)


def _approved(root, settings):
    """Keep this historical recovery fixture explicitly on its original run protocol."""
    with patch('tests.test_workspace_scratch.create_change', legacy_create_change):
        return scratch_approved(root, settings)


class RunResetTests(unittest.TestCase):
    def test_tainted_run_can_be_reset_and_then_passes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            change_id = _approved(root, {})
            with self.assertRaises(StoryLoopError):
                run_change(root, change_id, _services(change_id, build_in_place=True))
            self.assertEqual(load_run_state(root, change_id)["status"], "WORKSPACE_TAINTED")

            result = reset_run(root, change_id, "false positive: build output was declared later",
                               now=lambda: NOW)
            self.assertEqual(result["status"], "IMPLEMENTING")
            state = load_run_state(root, change_id)
            self.assertEqual(state["status"], "IMPLEMENTING")
            self.assertNotIn("error", state)
            self.assertEqual(state["resets"][0]["from_status"], "WORKSPACE_TAINTED")
            self.assertEqual(state["resets"][0]["reason"], "false positive: build output was declared later")
            self.assertEqual(state["resets"][0]["at"], "2026-09-19T00:00:00+00:00")

            done = run_change(root, change_id, _services(change_id, build_in_place=False))
            self.assertEqual(done["status"], "PASSED", done)
            # The interrupted attempt is not charged: the retry reuses attempt number 1.
            self.assertEqual(done["attempts"], 1)

    def test_reset_refuses_other_states_and_needs_a_reason(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            change_id = _approved(root, {})
            with self.assertRaises(StoryLoopError):
                reset_run(root, change_id, "no run yet")
            run_change(root, change_id, _services(change_id, build_in_place=False))
            self.assertEqual(load_run_state(root, change_id)["status"], "PASSED")
            with self.assertRaises(StoryLoopError):
                reset_run(root, change_id, "already passed")

    def test_reset_requires_the_feature_branch(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            change_id = _approved(root, {})
            source_branch = git(root, "branch", "--show-current")  # main or master, whatever git defaults to
            with self.assertRaises(StoryLoopError):
                run_change(root, change_id, _services(change_id, build_in_place=True))
            git(root, "stash", "-u", "-q")
            git(root, "checkout", "-q", source_branch)
            with self.assertRaises(StoryLoopError):
                reset_run(root, change_id, "wrong branch")
            with self.assertRaises(StoryLoopError):
                reset_run(root, change_id, "")


if __name__ == "__main__":
    unittest.main()
