"""Bounded, crash-safe replan behavior for v2 Stage retries."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from storyloop.models import StoryLoopError
from storyloop.replan import replan_stage, replan_receipt_path
from storyloop.stage_lifecycle import run_stage_lifecycle
from storyloop.stage_state import (
    begin_attempt,
    new_stage_state,
    resume_after_replan,
    settle_attempt_failure,
)
from tests.test_stage_config import loop_config
from tests.test_stage_lifecycle import green_result


def repeated_state(project: Path, config: dict, payload: dict | None = None) -> dict:
    """Build a valid repeated-failure state without touching persistence."""
    state = new_stage_state(project, config, "foundation", payload or {})
    for attempt in range(config["budgets"]["repeated_failure_limit"]):
        now_ms = max(1_000 + attempt * 100, state["next_eligible_at_ms"])
        running = begin_attempt(state, config, now_ms, 100, "a" * 64)
        state = settle_attempt_failure(
            running, config,
            StoryLoopError("private raw output", code="JUDGE_FAILED",
                           details={"evidence": {"stage": "foundation",
                                                   "judge": "core",
                                                   "secret": "do-not-persist"}}),
            10, now_ms + 10,
        )
    return state


class ReplanTests(unittest.TestCase):
    def test_resume_resets_only_batch_marker(self):
        config = loop_config()
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            state = repeated_state(project, config)
        resumed = resume_after_replan(state, config)
        self.assertEqual(resumed["status"], "READY")
        self.assertEqual(resumed["attempts_started"], state["attempts_started"])
        self.assertEqual(resumed["consumed_ms"], state["consumed_ms"])
        self.assertIsNone(resumed["last_failure_fingerprint"])
        self.assertEqual(resumed["repeated_failure_count"], 0)

    def test_receipt_prevents_duplicate_agent_invocation_after_crash(self):
        config = loop_config()
        calls = []

        def invoke(*args, **kwargs):
            calls.append((args, kwargs))
            return {"protocol": "loop.agent.v2", "request_id": "b" * 64,
                    "status": "ok", "summary": "retry safely", "questions": []}

        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            state = repeated_state(project, config)
            first = replan_stage(project, config, state, [], invoker=invoke)
            second = replan_stage(project, config, state, [], invoker=invoke)
            receipt = json.loads(replan_receipt_path(project, state).read_text("utf-8"))
        self.assertEqual(first, second)
        self.assertEqual(len(calls), 1)
        self.assertEqual(receipt["status"], "completed")
        self.assertNotIn("summary", receipt)
        self.assertNotIn("private raw output", json.dumps(receipt))

    def test_replan_payload_is_bounded_machine_evidence(self):
        config = loop_config()
        captured = []
        upstream = [{"stage": "prior", "result_digest": "c" * 64,
                     "changes": [], "judges": []}]

        def invoke(project, value, role, stage, payload, **kwargs):
            captured.append((role, stage, payload, kwargs))
            return {"protocol": "loop.agent.v2", "request_id": "d" * 64,
                    "status": "ok", "summary": "not persisted", "questions": []}

        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            state = repeated_state(project, config, {"secret": "never-forward"})
            replan_stage(project, config, state, upstream, invoker=invoke)
        role, stage, payload, _ = captured[0]
        self.assertEqual((role, stage), ("replan", "foundation"))
        self.assertEqual(payload["failure"]["code"], "JUDGE_FAILED")
        self.assertEqual(payload["failure"]["judges"], ["red", "core", "full"])
        self.assertEqual(payload["upstream"], upstream)
        self.assertNotIn("secret", json.dumps(payload))
        self.assertLess(len(json.dumps(payload)), 16_384)

    def test_nonterminal_replan_response_propagates_without_receipt(self):
        config = loop_config()

        def invoke(*args, **kwargs):
            raise StoryLoopError("choose architecture", code="ARCHITECTURE_DECISION_REQUIRED")

        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            state = repeated_state(project, config)
            with self.assertRaises(StoryLoopError) as caught:
                replan_stage(project, config, state, [], invoker=invoke)
            self.assertFalse(replan_receipt_path(project, state).exists())
        self.assertEqual(caught.exception.code, "ARCHITECTURE_DECISION_REQUIRED")

    def test_lifecycle_replans_one_batch_then_continues(self):
        config = loop_config()
        config["budgets"]["repeated_failure_limit"] = 1
        attempts = []
        replans = []

        def attempt(*args, **kwargs):
            attempts.append(True)
            if len(attempts) == 1:
                raise StoryLoopError("judge failed", code="JUDGE_FAILED")
            return green_result()

        def replan(project, value, state, upstream, **kwargs):
            replans.append((state["attempts_started"], upstream))
            return resume_after_replan(state, value)

        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            wall = iter([1_000, 1_010, 1_020, 1_030])
            mono = iter([0, 10, 20, 30])
            result = run_stage_lifecycle(
                project, config, "foundation", {}, attempt_runner=attempt,
                replan_runner=replan, upstream_handoffs=[], reserve_ms=100,
                wall_clock_ms=lambda: next(wall), monotonic_ms=lambda: next(mono),
            )
        self.assertEqual(result["status"], "STAGE_GREEN")
        self.assertEqual(len(attempts), 2)
        self.assertEqual(replans, [(1, [])])


if __name__ == "__main__":
    unittest.main()
