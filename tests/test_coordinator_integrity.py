"""Fail-closed coordinator state relationship tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from storyloop.coordinator_state import coordinator_path
from storyloop.models import StoryLoopError
from storyloop.stage_coordinator import run_stage_graph
from tests.test_stage_coordinator import graph_config, stage_green


class CoordinatorIntegrityTests(unittest.TestCase):
    def test_tampered_status_and_unsafe_handoff_path_are_rejected(self):
        for mutation in ("status", "path"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as folder:
                project = Path(folder)
                config = graph_config()
                run_stage_graph(
                    project, config, {}, target_stage="foundation",
                    require_governance=False, lifecycle_runner=lambda p, c, s, payload, **kwargs: stage_green(s),
                )
                path = coordinator_path(project)
                state = json.loads(path.read_text("utf-8"))
                record = state["stages"]["foundation"]
                if mutation == "status":
                    record["status"] = "FORGED"
                else:
                    record["handoff"]["changes"] = [
                        {"path": "../outside", "kind": "modified"}
                    ]
                path.write_text(json.dumps(state), encoding="utf-8")
                with self.assertRaises(StoryLoopError):
                    run_stage_graph(project, config, {}, target_stage="foundation",
                                    require_governance=False, lifecycle_runner=self.fail)

    def test_target_run_cannot_gain_an_unselected_stage_record(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            config = graph_config()
            run_stage_graph(
                project, config, {}, target_stage="foundation",
                require_governance=False, lifecycle_runner=lambda p, c, s, payload, **kwargs: stage_green(s),
            )
            path = coordinator_path(project)
            state = json.loads(path.read_text("utf-8"))
            forged = json.loads(json.dumps(state["stages"]["foundation"]))
            forged["handoff"]["stage"] = "delivery"
            state["stages"]["delivery"] = forged
            path.write_text(json.dumps(state), encoding="utf-8")
            with self.assertRaises(StoryLoopError):
                run_stage_graph(project, config, {}, target_stage="foundation",
                                require_governance=False, lifecycle_runner=self.fail)


if __name__ == "__main__":
    unittest.main()
