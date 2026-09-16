"""Bounded regular-file reads for v2 coordinator and replan control state."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from storyloop.coordinator_state import coordinator_path
from storyloop.models import StoryLoopError
from storyloop.replan import replan_receipt_path, replan_stage
from storyloop.stage_coordinator import run_stage_graph
from tests.test_replan import repeated_state
from tests.test_stage_config import loop_config


class ControlPlaneIoTests(unittest.TestCase):
    def test_oversized_coordinator_is_rejected_before_json_parsing(self):
        config = loop_config()
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            path = coordinator_path(project)
            path.parent.mkdir(parents=True)
            path.write_bytes(b"{" + b"x" * (1024 * 1024))
            with self.assertRaisesRegex(StoryLoopError, "size"):
                run_stage_graph(project, config, {}, require_governance=False, lifecycle_runner=self.fail)

    def test_oversized_replan_receipt_never_invokes_agent(self):
        config = loop_config()
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            state = repeated_state(project, config)
            path = replan_receipt_path(project, state)
            path.parent.mkdir(parents=True)
            path.write_bytes(b"{" + b"x" * (16 * 1024))
            with self.assertRaisesRegex(StoryLoopError, "size"):
                replan_stage(project, config, state, [],
                             invoker=lambda *args, **kwargs: self.fail("must not invoke"))


if __name__ == "__main__":
    unittest.main()
