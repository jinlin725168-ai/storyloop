"""Durable audit digests distinguish crash gaps from committed tampering."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from storyloop.models import StoryLoopError
from storyloop.run_artifacts import run_root
from storyloop.stage_lifecycle import run_persisted_attempt
from storyloop.stage_state import (
    begin_attempt, mark_stage_initialized, new_stage_state, save_stage_state,
    settle_attempt_success,
)
from tests.test_stage_config import loop_config
from tests.test_stage_lifecycle import green_result


def failing_attempt(*args, **kwargs):
    raise StoryLoopError("bounded failure", code="JUDGE_FAILED")


class ArtifactChainTests(unittest.TestCase):
    def _committed_failure(self, project: Path) -> tuple[dict, dict]:
        config = loop_config()
        config["budgets"]["repeated_failure_limit"] = 1
        wall = iter([1_000, 1_010])
        mono = iter([0, 10])
        state = run_persisted_attempt(
            project, config, "foundation", {}, requested_adapter="manual",
            attempt_runner=failing_attempt, reserve_ms=100,
            wall_clock_ms=lambda: next(wall), monotonic_ms=lambda: next(mono),
        )
        return config, state

    def test_committed_artifact_deletion_is_not_silently_rebuilt(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            config, state = self._committed_failure(project)
            self.assertEqual(len(state["artifact_chain"]), 1)
            artifact = run_root(project) / "foundation" / "attempt-1" / "status.json"
            artifact.unlink()
            with self.assertRaisesRegex(StoryLoopError, "tampered"):
                run_persisted_attempt(
                    project, config, "foundation", {}, requested_adapter="manual",
                )

    def test_extra_entry_in_committed_attempt_is_tampering(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            config, _ = self._committed_failure(project)
            attempt = run_root(project) / "foundation" / "attempt-1"
            (attempt / "unexpected").mkdir()
            with self.assertRaisesRegex(StoryLoopError, "tampered"):
                run_persisted_attempt(
                    project, config, "foundation", {}, requested_adapter="manual",
                )


    def test_unbound_crash_gap_is_recovered_and_then_bound(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            config = loop_config()
            state = new_stage_state(project, config, "foundation", {})
            state = begin_attempt(state, config, 1_000, 100, "a" * 64)
            state = settle_attempt_success(
                state, config, green_result(), 10, 1_010,
            )
            save_stage_state(project, state)
            mark_stage_initialized(project, "foundation")
            recovered = run_persisted_attempt(
                project, config, "foundation", {}, requested_adapter="manual",
            )
            self.assertEqual(len(recovered["artifact_chain"]), 1)

    def test_every_prior_bound_attempt_is_verified(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            config = loop_config()
            config["budgets"]["repeated_failure_limit"] = 3
            for wall_values in ((1_000, 1_010), (3_010, 3_020)):
                wall, mono = iter(wall_values), iter((0, 10))
                state = run_persisted_attempt(
                    project, config, "foundation", {}, requested_adapter="manual",
                    attempt_runner=failing_attempt, reserve_ms=100,
                    wall_clock_ms=lambda: next(wall),
                    monotonic_ms=lambda: next(mono),
                )
            self.assertEqual([item["attempt"] for item in state["artifact_chain"]],
                             [1, 2])
            path = run_root(project) / "foundation" / "attempt-1" / "status.json"
            value = json.loads(path.read_text("utf-8"))
            value["status"] = "FAILED"
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(StoryLoopError, "tampered"):
                run_persisted_attempt(
                    project, config, "foundation", {}, requested_adapter="manual",
                    wall_clock_ms=lambda: 8_020,
                    attempt_runner=lambda *args, **kwargs: self.fail("must not execute"),
                )

    def test_unknown_run_root_entry_is_tampering(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            config, _ = self._committed_failure(project)
            (run_root(project) / "rogue").mkdir()
            with self.assertRaisesRegex(StoryLoopError, "tampered"):
                run_persisted_attempt(
                    project, config, "foundation", {}, requested_adapter="manual",
                )

if __name__ == "__main__":
    unittest.main()
