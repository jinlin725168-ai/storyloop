"""Persistent DAG coordination tests for v2 Stages."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from storyloop.coordinator_state import coordinator_path
from storyloop.models import StoryLoopError
from storyloop.stage_coordinator import run_stage_graph
from tests.test_stage_config import loop_config
from tests.test_stage_lifecycle import green_result
from tests.test_stage_runner import judge_evidence


def graph_config() -> dict:
    config = loop_config()
    second = {
        **config["stages"][0],
        "id": "delivery",
        "depends_on": ["foundation"],
        "specs": ["openspec/changes/delivery.md"],
        "write_paths": ["service/delivery/**"],
        "protected_paths": ["openspec/changes/delivery.md", "acceptance/**"],
        "judges": [dict(item) for item in config["stages"][0]["judges"]],
    }
    config["stages"].append(second)
    return config


def stage_green(stage_id: str) -> dict:
    result = green_result()
    result["stage"] = stage_id
    for evidence in [result["red"], *result["l1"], *result["l2"]]:
        evidence["stage"] = stage_id
    return {"status": "STAGE_GREEN", "result": result}


class StageCoordinatorTests(unittest.TestCase):
    def test_runs_dependencies_in_order_and_injects_bounded_handoff(self):
        calls = []

        def lifecycle(project, config, stage_id, payload, **kwargs):
            calls.append((stage_id, payload, kwargs))
            return stage_green(stage_id)

        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder) / "project"
            project.mkdir()
            state = run_stage_graph(
                project, graph_config(),
                {"foundation": {"goal": "base"}, "delivery": {"goal": "ship"}},
                target_stage="delivery", require_governance=False, lifecycle_runner=lifecycle,
            )

        self.assertEqual([item[0] for item in calls], ["foundation", "delivery"])
        self.assertEqual(calls[1][1]["upstream"][0]["stage"], "foundation")
        self.assertEqual(calls[1][2]["upstream_handoffs"], calls[1][1]["upstream"])
        self.assertNotIn("agent_response", str(calls[1][1]["upstream"]))
        self.assertEqual(state["status"], "PROJECT_GREEN")

    def test_resume_skips_green_stage_and_never_runs_blocked_dependent(self):
        calls = []

        def first(project, config, stage_id, payload, **kwargs):
            calls.append(stage_id)
            return stage_green(stage_id) if stage_id == "foundation" else {
                "status": "NEEDS_INPUT", "result": None,
            }

        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder) / "project"
            project.mkdir()
            waiting = run_stage_graph(
                project, graph_config(), {}, require_governance=False, lifecycle_runner=first,
            )
            resumed_calls = []
            resumed = run_stage_graph(
                project, graph_config(), {},
                require_governance=False, lifecycle_runner=lambda project, config, stage_id, payload, **kwargs: (
                    resumed_calls.append(stage_id) or stage_green(stage_id)
                ),
            )

        self.assertEqual(waiting["status"], "NEEDS_INPUT")
        self.assertEqual(calls, ["foundation", "delivery"])
        self.assertEqual(resumed_calls, ["delivery"])
        self.assertEqual(resumed["status"], "PROJECT_GREEN")

    def test_final_runs_only_after_graph_green_and_is_not_repeated(self):
        config = graph_config()
        config["stages"][1]["judges"].append(
            {"id": "final", "phase": "final", "command": ["python", "final.py"]}
        )
        final_item = judge_evidence("final", "final", "GREEN")
        final_item["stage"] = "delivery"
        final = [final_item]
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder) / "project"
            project.mkdir()
            with patch("storyloop.stage_coordinator.run_final_judges",
                       return_value=final) as run_final:
                state = run_stage_graph(
                    project, config, {},
                    require_governance=False, lifecycle_runner=lambda p, c, s, payload, **kwargs: stage_green(s),
                )
                again = run_stage_graph(
                    project, config, {},
                    require_governance=False, lifecycle_runner=lambda *args, **kwargs: self.fail("must not rerun"),
                )
        self.assertEqual(state["status"], "PROJECT_GREEN")
        self.assertEqual(state["final"][0]["judge"], "final")
        self.assertNotIn("summary", state["final"][0])
        self.assertEqual(again, state)
        run_final.assert_called_once()

    def test_tampered_final_evidence_is_rejected_on_reload(self):
        config = graph_config()
        config["stages"][1]["judges"].append(
            {"id": "final", "phase": "final", "command": ["python", "final.py"]}
        )
        evidence = judge_evidence("final", "final", "GREEN")
        evidence["stage"] = "delivery"
        with tempfile.TemporaryDirectory() as folder, patch(
            "storyloop.stage_coordinator.run_final_judges", return_value=[evidence]
        ):
            project = Path(folder) / "project"
            project.mkdir()
            run_stage_graph(
                project, config, {},
                require_governance=False, lifecycle_runner=lambda p, c, s, payload, **kwargs: stage_green(s),
            )
            path = coordinator_path(project)
            state = json.loads(path.read_text(encoding="utf-8"))
            state["final"][0]["judge"] = "forged"
            path.write_text(json.dumps(state), encoding="utf-8")
            with self.assertRaises(StoryLoopError):
                run_stage_graph(project, config, {}, require_governance=False, lifecycle_runner=self.fail)
    def test_waiting_stage_never_runs_final(self):
        config = graph_config()
        with tempfile.TemporaryDirectory() as folder, patch(
            "storyloop.stage_coordinator.run_final_judges"
        ) as run_final:
            project = Path(folder) / "project"
            project.mkdir()
            state = run_stage_graph(
                project, config, {},
                require_governance=False, lifecycle_runner=lambda p, c, s, payload, **kwargs: {
                    "status": "NEEDS_INPUT", "result": None,
                },
            )
        self.assertEqual(state["status"], "NEEDS_INPUT")
        run_final.assert_not_called()
    def test_tampered_handoff_is_rejected_before_resume(self):
        config = graph_config()
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder) / "project"
            project.mkdir()
            run_stage_graph(
                project, config, {},
                require_governance=False, lifecycle_runner=lambda p, c, s, payload, **kwargs: stage_green(s),
            )
            path = coordinator_path(project)
            state = json.loads(path.read_text(encoding="utf-8"))
            state["stages"]["foundation"]["handoff"]["secret"] = "leak"
            path.write_text(json.dumps(state), encoding="utf-8")
            with self.assertRaises(StoryLoopError):
                run_stage_graph(
                    project, config, {},
                    require_governance=False, lifecycle_runner=lambda *args, **kwargs: self.fail("must not run"),
                )
    def test_changed_graph_identity_fails_instead_of_resetting_progress(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder) / "project"
            project.mkdir()
            run_stage_graph(
                project, graph_config(), {},
                require_governance=False, lifecycle_runner=lambda p, c, s, payload, **kwargs: stage_green(s),
            )
            changed = graph_config()
            changed["budgets"]["total_seconds"] += 1
            with self.assertRaisesRegex(StoryLoopError, "identity"):
                run_stage_graph(
                    project, changed, {},
                    require_governance=False, lifecycle_runner=lambda *args, **kwargs: self.fail("must not run"),
                )


if __name__ == "__main__":
    unittest.main()
