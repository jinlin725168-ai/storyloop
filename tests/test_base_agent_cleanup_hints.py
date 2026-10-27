"""Unsafe anchors and retryable cleanup failures give different recovery commands."""

import unittest
from unittest import mock

from storyloop import base_agent_execution, run_recovery
from storyloop.models import StoryLoopError
from storyloop.run_state import run_path
from storyloop.storage import load_json
from tests import test_base_agent_recovery as recovery
from tests.test_base_agent_flow import run_change


class BaseAgentCleanupHintTests(unittest.TestCase):
    setUp = recovery.BaseAgentRecoveryTests.setUp
    _approved = recovery.BaseAgentRecoveryTests._approved
    _services = recovery.BaseAgentRecoveryTests._services
    _writer = recovery.BaseAgentRecoveryTests._writer
    _successful_writer = recovery.BaseAgentRecoveryTests._successful_writer

    def test_cleanup_failure_instructs_run_without_discarding_candidate(self):
        self._approved()
        with mock.patch.object(base_agent_execution, "remove_agent_worktree",
                               side_effect=StoryLoopError("locked checkout", code="WORKSPACE_TAINTED")):
            result = run_change(self.root, self.change["change_id"],
                                services=self._successful_writer())
        self.assertEqual(result["status"], "WORKSPACE_TAINTED")
        self.assertIn("storyloop run", result["next"])
        self.assertNotIn("storyloop reset", result["next"])
        self.assertIn("candidate_commit", load_json(run_path(self.root, self.change["change_id"])))

    def test_invalid_anchor_after_saved_candidate_instructs_manual_reset(self):
        self._approved()
        invalid = StoryLoopError("preserve anchor; storyloop reset", code="AGENT_ANCHOR_INVALID")
        with mock.patch.object(base_agent_execution, "remove_agent_worktree", side_effect=invalid):
            result = run_change(self.root, self.change["change_id"],
                                services=self._successful_writer())
        self.assertEqual(result["status"], "WORKSPACE_TAINTED")
        self.assertIn("storyloop reset", result["next"])
        self.assertNotIn("storyloop run", result["next"])

    def test_invalid_anchor_during_cleanup_retry_instructs_manual_reset(self):
        self._approved()
        with mock.patch.object(base_agent_execution, "remove_agent_worktree",
                               side_effect=StoryLoopError("locked checkout")):
            run_change(self.root, self.change["change_id"],
                       services=self._successful_writer())
        invalid = StoryLoopError("preserve anchor; storyloop reset", code="AGENT_ANCHOR_INVALID")
        with mock.patch("storyloop.base_agent_worktree.remove_agent_worktree", side_effect=invalid):
            result = run_change(self.root, self.change["change_id"], services=self._services())
        self.assertEqual(result["status"], "WORKSPACE_TAINTED")
        self.assertIn("storyloop reset", result["next"])
        self.assertNotIn("storyloop run", result["next"])

    def test_restart_cleanup_hint_matches_saved_candidate_verification(self):
        self._approved()
        change_id = self.change["change_id"]
        state = load_json(run_path(self.root, change_id))
        state["candidate_commit"] = state["contract_commit"]
        state["base_inflight"] = "candidate_verification"
        with mock.patch.object(run_recovery, "remove_agent_worktree",
                               side_effect=StoryLoopError("cleanup failed; storyloop run")):
            self.assertEqual(run_recovery._restart_agent_base(self.root, state),
                             "WORKSPACE_TAINTED")
        self.assertIn("storyloop run", state["next"])
        self.assertNotIn("storyloop reset", state["next"])

    def test_restart_cleanup_without_candidate_instructs_reset(self):
        self._approved()
        state = load_json(run_path(self.root, self.change["change_id"]))
        state["base_inflight"] = "agent_implementation"
        with mock.patch.object(run_recovery, "remove_agent_worktree",
                               side_effect=StoryLoopError("cleanup failed; storyloop reset")):
            self.assertEqual(run_recovery._restart_agent_base(self.root, state),
                             "WORKSPACE_TAINTED")
        self.assertIn("storyloop reset", state["next"])
