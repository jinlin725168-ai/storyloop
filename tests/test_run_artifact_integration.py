"""Integration coverage for automatic v2 audit emission."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from storyloop.constitution import generate_constitutions
from storyloop.models import StoryLoopError
from storyloop.run_artifacts import run_root
from storyloop.stage_coordinator import run_stage_graph
from storyloop.v2_cli import read_stage_status
from storyloop.stage_lifecycle import run_persisted_attempt
from tests.test_spec_gate import proposal
from tests.test_stage_runner import judge_evidence
from tests.test_stage_config import loop_config
from tests.test_stage_lifecycle import green_result


class RunArtifactIntegrationTests(unittest.TestCase):
    def test_persisted_attempt_automatically_commits_audit_after_state(self):
        config = loop_config()
        config["budgets"]["repeated_failure_limit"] = 1
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            spec = project / config["stages"][0]["specs"][0]
            spec.parent.mkdir(parents=True)
            spec.write_text(proposal(), encoding="utf-8")
            source = project / "governance/L1.md"
            source.parent.mkdir()
            source.write_text("# L1\n\nGoverned rules.\n", encoding="utf-8")
            generate_constitutions(project)
            wall = iter([1_000, 1_010])
            mono = iter([0, 10])
            state = run_persisted_attempt(
                project, config, "foundation", {}, requested_adapter="manual",
                attempt_runner=lambda *args, **kwargs: (_ for _ in ()).throw(
                    StoryLoopError("raw private failure", code="JUDGE_FAILED")
                ), reserve_ms=100, wall_clock_ms=lambda: next(wall),
                monotonic_ms=lambda: next(mono),
            )
            artifact = run_root(project) / "foundation" / "attempt-1" / "status.json"
            self.assertTrue(artifact.is_file())
            self.assertEqual(state["status"], "REPEATED_FAILURE")

    def test_coordinator_automatically_commits_final_artifact(self):
        config = loop_config()
        config["stages"][0]["judges"].append(
            {"id": "release", "phase": "final", "command": ["python", "final.py"]}
        )
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            spec = project / config["stages"][0]["specs"][0]
            spec.parent.mkdir(parents=True)
            spec.write_text(proposal(), encoding="utf-8")
            source = project / "governance/L1.md"
            source.parent.mkdir()
            source.write_text("# L1\n\nGoverned rules.\n", encoding="utf-8")
            generate_constitutions(project)
            with patch("storyloop.stage_coordinator.run_final_judges",
                       return_value=[judge_evidence("release", "final", "GREEN")]):
                result = run_stage_graph(
                    project, config, {"foundation": {}},
                    lifecycle_runner=lambda *args, **kwargs: {
                        **green_result(), "status": "STAGE_GREEN", "result": green_result(),
                    },
                )
            artifact = run_root(project) / "final" / "evidence.json"
            self.assertTrue(artifact.is_file())
            self.assertEqual(result["status"], "PROJECT_GREEN")
            artifact.unlink()
            with patch("storyloop.coordinator_state.verify_coordinator_records"):
                with self.assertRaisesRegex(StoryLoopError, "tampered"):
                    read_stage_status(project, config, {"foundation": {}}, None)
            with self.assertRaisesRegex(StoryLoopError, "tampered"):
                run_stage_graph(
                    project, config, {"foundation": {}},
                    lifecycle_runner=self.fail,
                )


if __name__ == "__main__":
    unittest.main()
