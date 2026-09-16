"""Auditable, non-authoritative v2 run artifact tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from storyloop.models import StoryLoopError
from storyloop.run_artifacts import (
    attempt_metrics,
    record_final_run,
    record_stage_attempt,
    run_root,
)
from storyloop.stage_state import begin_attempt, new_stage_state, settle_attempt_failure
from tests.test_replan import repeated_state
from tests.test_stage_config import loop_config
from tests.test_stage_lifecycle import green_result


class RunArtifactTests(unittest.TestCase):
    def test_attempt_layout_is_deterministic_bounded_and_redacted(self):
        config = loop_config()
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            state = repeated_state(project, config)
            record_stage_attempt(project, config, state, "codex")
            attempt = run_root(project) / "foundation" / "attempt-2"
            status = json.loads((attempt / "status.json").read_text("utf-8"))
            encoded = (attempt / "status.json").read_text("utf-8")
        self.assertEqual(status["attempt"], 2)
        self.assertEqual(status["status"], "REPEATED_FAILURE")
        self.assertEqual(status["adapter"], "codex")
        self.assertNotIn("private raw output", encoded)
        self.assertNotIn("secret", encoded.lower())
        self.assertLess(len(encoded.encode("utf-8")), 65_536)

    def test_green_attempt_separates_red_and_green_evidence(self):
        config = loop_config()
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            state = new_stage_state(project, config, "foundation", {})
            state = begin_attempt(state, config, 1_000, 100, "a" * 64)
            state = {**state, "status": "STAGE_GREEN", "reserved_ms": 0,
                     "consumed_ms": 20, "last_wall_time_ms": 1_020,
                     "attempt_workspace_digest": None,
                     "result": green_result()}
            record_stage_attempt(project, config, state, "manual")
            attempt = run_root(project) / "foundation" / "attempt-1"
            red = json.loads((attempt / "red.json").read_text("utf-8"))
            green = json.loads((attempt / "green.json").read_text("utf-8"))
        self.assertEqual(red["evidence"]["phase"], "red")
        self.assertEqual([item["phase"] for item in green["judges"]], ["l1", "l2"])
        self.assertNotIn("agent_response", json.dumps(green))

    def test_status_commit_marker_detects_tampering(self):
        config = loop_config()
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            state = repeated_state(project, config)
            record_stage_attempt(project, config, state, "codex")
            path = run_root(project) / "foundation" / "attempt-2" / "status.json"
            value = json.loads(path.read_text("utf-8"))
            value["status"] = "STAGE_GREEN"
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(StoryLoopError, "tampered"):
                record_stage_attempt(project, config, state, "codex")

    def test_metrics_are_comparable_without_tokens(self):
        config = loop_config()
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            state = repeated_state(project, config)
            record_stage_attempt(project, config, state, "claude")
            metrics = attempt_metrics(project, config)
        self.assertEqual(metrics[0]["adapter"], "claude")
        self.assertEqual(metrics[0]["attempt"], 2)
        self.assertIn("cumulative_duration_ms", metrics[0])
        self.assertNotIn("tokens", metrics[0])

    def test_final_evidence_has_separate_bounded_artifact(self):
        config = loop_config()
        config["stages"][0]["judges"].append(
            {"id": "release", "phase": "final", "command": ["python", "final.py"]}
        )
        final = {"protocol": "loop.coordinator.v2", "status": "PROJECT_GREEN",
                 "stages": {"foundation": {"status": "STAGE_GREEN",
                    "handoff": {"stage": "foundation", "result_digest": "a" * 64,
                                "changes": [], "judges": []}}},
                 "final": [{"request_id": "b" * 64, "stage": "foundation",
                    "judge": "release", "phase": "final", "status": "pass",
                    "outcome": "GREEN", "exit_code": 0, "contained": True,
                    "command_digest": "c" * 64, "before_digest": "d" * 64,
                    "after_digest": "d" * 64}]}
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            record_final_run(project, config, final)
            artifact = json.loads(
                (run_root(project) / "final" / "evidence.json").read_text("utf-8")
            )
        self.assertEqual(artifact["status"], "PROJECT_GREEN")
        self.assertEqual(artifact["stage_result_digests"], ["a" * 64])
        self.assertEqual(artifact["judges"][0]["judge"], "release")


if __name__ == "__main__":
    unittest.main()
