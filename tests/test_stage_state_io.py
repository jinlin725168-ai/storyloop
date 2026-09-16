"""Bounded and link-safe I/O tests for v2 Stage state."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from storyloop.models import StoryLoopError
from storyloop.stage_state import (
    begin_attempt,
    load_stage_state,
    new_stage_state,
    resume_nonterminal_state,
    save_stage_state,
    settle_attempt_success,
    settle_attempt_tainted,
    stage_state_path,
)
from tests.test_stage_config import loop_config
from tests.test_stage_lifecycle import green_result


class StageStateIoTests(unittest.TestCase):
    def test_green_result_must_match_the_configured_judge_set(self):
        config = loop_config()
        for mutation in ("empty_l1", "wrong_judge"):
            with self.subTest(mutation=mutation):
                state = new_stage_state(Path.cwd(), config, "foundation", {})
                state = begin_attempt(state, config, 1_000, 1_000, "a" * 64)
                result = green_result()
                if mutation == "empty_l1":
                    result["l1"] = []
                else:
                    result["l2"][0]["judge"] = "other"
                with self.assertRaises(StoryLoopError):
                    settle_attempt_success(state, config, result, 100, 1_100)

    def test_tainted_state_requires_restored_workspace_before_resume(self):
        config = loop_config()
        baseline = "a" * 64
        running = begin_attempt(
            new_stage_state(Path.cwd(), config, "foundation", {}),
            config, now_ms=1_000, reserve_ms=4_000,
            workspace_digest=baseline,
        )
        tainted = settle_attempt_tainted(
            running, config,
            StoryLoopError("changed", code="WORKSPACE_TAINTED"),
            elapsed_ms=100, now_ms=1_100,
        )
        self.assertEqual(tainted["attempt_workspace_digest"], baseline)
        with self.assertRaisesRegex(StoryLoopError, "(?i)restore"):
            resume_nonterminal_state(
                tainted, acknowledge_unsafe=True, workspace_digest="b" * 64,
            )
        resumed = resume_nonterminal_state(
            tainted, acknowledge_unsafe=True, workspace_digest=baseline,
        )
        self.assertEqual(resumed["status"], "READY")
        self.assertIsNone(resumed["attempt_workspace_digest"])
    def test_save_rejects_document_larger_than_state_limit(self):
        config = loop_config()
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder) / "project"
            project.mkdir()
            state = new_stage_state(project, config, "foundation", {})
            with patch("storyloop.stage_state.MAX_STATE_BYTES", 100):
                with self.assertRaisesRegex(StoryLoopError, "size limit"):
                    save_stage_state(project, state)
            self.assertFalse(stage_state_path(project, "foundation").exists())

    def test_load_does_not_use_unbounded_path_read_bytes(self):
        config = loop_config()
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder) / "project"
            project.mkdir()
            state = new_stage_state(project, config, "foundation", {})
            save_stage_state(project, state)
            with patch.object(
                Path, "read_bytes", side_effect=AssertionError("unbounded read")
            ):
                loaded = load_stage_state(project, config, "foundation", {})
            self.assertEqual(loaded, state)


if __name__ == "__main__":
    unittest.main()
