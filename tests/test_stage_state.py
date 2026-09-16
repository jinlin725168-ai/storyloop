"""Durable identity and storage tests for one v2 Stage lifecycle."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from storyloop.models import StoryLoopError
from storyloop.stage_agents import adapter_control_root, invoke_selected_adapter
from storyloop.stage_lifecycle import run_persisted_attempt, run_stage_lifecycle
from storyloop.stage_state import (
    begin_attempt,
    load_stage_state,
    locked_stage_state,
    new_stage_state,
    recover_interrupted_attempt,
    resume_nonterminal_state,
    save_stage_state,
    settle_attempt_failure,
    settle_attempt_nonterminal,
    settle_attempt_success,
    stage_state_path,
)
from tests.test_stage_config import loop_config
from tests.test_stage_runner import judge_evidence


def green_result(agent_response: dict | None = None) -> dict:
    return {
        "stage": "foundation", "outcome": "STAGE_GREEN",
        "agent_invoked": True, "agent_response": agent_response or {"status": "ok"},
        "changes": [],
        "red": judge_evidence("red", "red", "RED"),
        "l1": [judge_evidence("core", "l1", "GREEN")],
        "l2": [judge_evidence("full", "l2", "GREEN")],
    }

class StageStateTests(unittest.TestCase):
    def test_state_is_external_bound_and_contains_only_payload_digest(self):
        config = loop_config()
        payload = {"goal": "synthetic-private-value"}

        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder) / "project"
            project.mkdir()
            state = new_stage_state(project, config, "foundation", payload)
            save_stage_state(project, state)
            path = stage_state_path(project, "foundation")
            loaded = load_stage_state(project, config, "foundation", payload)
            serialized = path.read_text(encoding="utf-8")

        self.assertFalse(path.is_relative_to(project))
        self.assertEqual(loaded, state)
        self.assertNotIn("synthetic-private-value", serialized)
        self.assertEqual(loaded["protocol"], "loop.state.v2")
        self.assertEqual(loaded["status"], "READY")

    def test_invalid_state_fields_are_rejected_before_write(self):
        config = loop_config()
        cases = (
            ("status", "MADE_UP"),
            ("status", []),
            ("attempts_started", -1),
            ("consumed_ms", -1),
            ("reserved_ms", -1),
            ("repeated_failure_count", -1),
            ("next_eligible_at_ms", -1),
            ("last_wall_time_ms", -1),
            ("project_id", "z" * 64),
        )

        for field, value in cases:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as folder:
                project = Path(folder) / "project"
                project.mkdir()
                state = new_stage_state(project, config, "foundation", {})
                state[field] = value
                with self.assertRaises(StoryLoopError):
                    save_stage_state(project, state)
                self.assertFalse(stage_state_path(project, "foundation").exists())
    def test_invalid_state_relationships_are_rejected(self):
        config = loop_config()
        cases = (
            {"status": "RUNNING"},
            {"reserved_ms": 1},
            {"status": "BACKOFF"},
            {"last_failure_fingerprint": "a" * 64},
            {"repeated_failure_count": 1},
        )
        for changes in cases:
            with self.subTest(changes=changes), tempfile.TemporaryDirectory() as folder:
                project = Path(folder) / "project"
                project.mkdir()
                state = new_stage_state(project, config, "foundation", {})
                state.update(changes)
                with self.assertRaises(StoryLoopError):
                    save_stage_state(project, state)

    def test_missing_state_fails_with_safe_workflow_error(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder) / "project"
            project.mkdir()
            with self.assertRaisesRegex(StoryLoopError, "Cannot read Stage state"):
                load_stage_state(project, loop_config(), "foundation", {})
    def test_external_state_lock_rejects_a_second_runner(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder) / "project"
            project.mkdir()
            with locked_stage_state(project, "foundation") as lock_path:
                self.assertFalse(lock_path.is_relative_to(project))
                with self.assertRaisesRegex(StoryLoopError, "Another operation"):
                    with locked_stage_state(project, "foundation"):
                        self.fail("second runner acquired the same Stage lock")
    def test_interrupted_attempt_keeps_its_reserved_time_charge(self):
        config = loop_config()
        config["budgets"]["total_seconds"] = 10
        state = new_stage_state(Path.cwd(), config, "foundation", {})

        started = begin_attempt(state, config, now_ms=1_000, reserve_ms=4_000, workspace_digest="a" * 64)
        recovered = recover_interrupted_attempt(
            started, config, now_ms=2_000, workspace_digest="a" * 64,
        )

        self.assertEqual(state["status"], "READY")
        self.assertEqual(started["attempts_started"], 1)
        self.assertEqual(started["reserved_ms"], 4_000)
        self.assertEqual(started["consumed_ms"], 0)
        self.assertEqual(recovered["status"], "BACKOFF")
        self.assertEqual(recovered["reserved_ms"], 0)
        self.assertEqual(recovered["consumed_ms"], 4_000)
        self.assertEqual(recovered["repeated_failure_count"], 1)
    def test_machine_failure_fingerprint_drives_backoff_and_repeat_limit(self):
        config = loop_config()
        config["budgets"]["total_seconds"] = 10
        evidence = {
            "stage": "foundation", "judge": "core", "phase": "l1",
            "status": "fail", "outcome": "FAIL", "exit_code": 1,
            "command_digest": "a" * 64, "before_digest": "b" * 64,
            "after_digest": "b" * 64,
        }
        state = new_stage_state(Path.cwd(), config, "foundation", {})
        first = begin_attempt(state, config, now_ms=1_000, reserve_ms=4_000, workspace_digest="a" * 64)
        first = settle_attempt_failure(
            first, config,
            StoryLoopError("wording one", code="JUDGE_FAILED",
                           details={"evidence": evidence}),
            elapsed_ms=500, now_ms=1_500,
        )

        self.assertEqual(first["status"], "BACKOFF")
        self.assertEqual(first["consumed_ms"], 500)
        self.assertEqual(first["reserved_ms"], 0)
        self.assertEqual(first["next_eligible_at_ms"], 3_500)
        with self.assertRaisesRegex(StoryLoopError, "backoff"):
            begin_attempt(first, config, now_ms=3_499, reserve_ms=1_000, workspace_digest="a" * 64)

        second = begin_attempt(first, config, now_ms=3_500, reserve_ms=1_000, workspace_digest="a" * 64)
        second = settle_attempt_failure(
            second, config,
            StoryLoopError("different wording", code="JUDGE_FAILED",
                           details={"evidence": evidence}),
            elapsed_ms=250, now_ms=3_750,
        )
        self.assertEqual(second["status"], "REPEATED_FAILURE")
        self.assertEqual(second["repeated_failure_count"], 2)
        self.assertEqual(second["consumed_ms"], 750)
    def test_nonterminal_states_refund_attempt_and_unsafe_requires_ack(self):
        config = loop_config()
        for code in (
            "NEEDS_INPUT", "ARCHITECTURE_DECISION_REQUIRED", "UNSAFE_CONTAINMENT",
        ):
            with self.subTest(code=code):
                ready = new_stage_state(Path.cwd(), config, "foundation", {})
                running = begin_attempt(ready, config, now_ms=1_000, reserve_ms=4_000, workspace_digest="a" * 64)
                waiting = settle_attempt_nonterminal(
                    running, config, StoryLoopError("private detail", code=code),
                    elapsed_ms=100, now_ms=1_100,
                )
                self.assertEqual(waiting["status"], code)
                self.assertEqual(waiting["attempts_started"], 0)
                self.assertEqual(waiting["consumed_ms"], 100)
                self.assertNotIn("private detail", str(waiting))
                if code == "UNSAFE_CONTAINMENT":
                    with self.assertRaisesRegex(StoryLoopError, "acknowledgement"):
                        resume_nonterminal_state(waiting)
                    resumed = resume_nonterminal_state(
                        waiting, acknowledge_unsafe=True, workspace_digest="a" * 64,
                    )
                else:
                    resumed = resume_nonterminal_state(waiting)
                self.assertEqual(resumed["status"], "READY")

    def test_stage_lifecycle_sleeps_outside_attempt_and_retries_to_green(self):
        config = loop_config()
        config["budgets"]["total_seconds"] = 10
        now = [1_000]
        monotonic = [0]
        sleeps = []
        calls = []
        result = green_result()


        def clock():
            return now[0]

        def monotonic_clock():
            value = monotonic[0]
            monotonic[0] += 100
            return value

        def sleep(seconds):
            sleeps.append(seconds)
            now[0] += int(seconds * 1000)

        def attempt(*args, **kwargs):
            calls.append(len(calls) + 1)
            if len(calls) == 1:
                raise StoryLoopError("judge failed", code="JUDGE_FAILED")
            return result

        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder) / "project"
            project.mkdir()
            state = run_stage_lifecycle(
                project, config, "foundation", {}, attempt_runner=attempt,
                reserve_ms=1_000, wall_clock_ms=clock,
                monotonic_ms=monotonic_clock, sleeper=sleep,
            )

        self.assertEqual(state["status"], "STAGE_GREEN")
        self.assertEqual(state["attempts_started"], 2)
        self.assertEqual(state["consumed_ms"], 200)
        self.assertEqual(calls, [1, 2])
        self.assertEqual(sleeps, [2.0])

    def test_persisted_nonterminal_resumes_only_when_explicitly_requested(self):
        config = loop_config()
        config["budgets"]["total_seconds"] = 10
        result = green_result()


        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder) / "project"
            project.mkdir()
            first_wall = iter([1_000, 1_100])
            first_mono = iter([0, 100])
            waiting = run_persisted_attempt(
                project, config, "foundation", {},
                attempt_runner=lambda *args, **kwargs: (_ for _ in ()).throw(
                    StoryLoopError("question", code="NEEDS_INPUT")
                ),
                reserve_ms=4_000,
                wall_clock_ms=lambda: next(first_wall),
                monotonic_ms=lambda: next(first_mono),
            )
            blocked = run_persisted_attempt(
                project, config, "foundation", {},
                attempt_runner=lambda *args, **kwargs: self.fail(
                    "Waiting state must not resume implicitly"
                ),
                wall_clock_ms=lambda: 1_200, monotonic_ms=lambda: 0,
            )
            second_wall = iter([1_300, 1_400])
            second_mono = iter([0, 100])
            resumed = run_persisted_attempt(
                project, config, "foundation", {},
                attempt_runner=lambda *args, **kwargs: result,
                reserve_ms=4_000, resume_waiting=True,
                wall_clock_ms=lambda: next(second_wall),
                monotonic_ms=lambda: next(second_mono),
            )

        self.assertEqual(waiting["status"], "NEEDS_INPUT")
        self.assertEqual(blocked["status"], "NEEDS_INPUT")
        self.assertEqual(resumed["status"], "STAGE_GREEN")
        self.assertEqual(resumed["attempts_started"], 1)

    def test_existing_green_consumes_a_prior_valid_manual_response(self):
        config = loop_config()
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder) / "project"
            project.mkdir()
            with self.assertRaises(StoryLoopError):
                invoke_selected_adapter(
                    project, config, "implement", "foundation", {},
                    requested="manual",
                )
            root = adapter_control_root(project)
            request_path = root / "requests" / "foundation.implement.json"
            response_path = root / "responses" / "foundation.implement.json"
            request = json.loads(request_path.read_text(encoding="utf-8"))
            response_path.parent.mkdir(parents=True)
            response_path.write_text(json.dumps({
                "request_id": request["request_id"],
                "result": {
                    "protocol": "loop.agent.v2", "request_id": request["request_id"],
                    "status": "ok", "summary": "done", "questions": [],
                },
            }), encoding="utf-8")
            result = green_result()
            result.update({
                "agent_invoked": False, "agent_response": None,
                "red": judge_evidence("red", "red", "EXISTING_GREEN"),
            })
            wall_values = iter([1_000, 1_100])
            monotonic_values = iter([0, 100])
            state = run_persisted_attempt(
                project, config, "foundation", {},
                attempt_runner=lambda *args, **kwargs: result,
                requested_adapter="manual",
                reserve_ms=1_000,
                wall_clock_ms=lambda: next(wall_values),
                monotonic_ms=lambda: next(monotonic_values),
            )

            self.assertEqual(state["status"], "STAGE_GREEN")
            self.assertIsNone(state["pending_manual_ack"])
            self.assertFalse(response_path.exists())

    def test_manual_ack_recovers_after_green_state_was_persisted(self):
        config = loop_config()
        config["budgets"]["total_seconds"] = 10
        acknowledgement = {
            "protocol": "loop.manual.ack.v2", "stage": "foundation",
            "role": "implement", "request_id": "9" * 64,
        }
        result = green_result(
            {"status": "ok", "_manual_ack": acknowledgement},
        )

        wall_values = iter([1_000, 1_100])
        monotonic_values = iter([0, 100])
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder) / "project"
            project.mkdir()

            def observe_green_then_crash(target, ack):
                persisted = load_stage_state(project, config, "foundation", {})
                self.assertEqual(persisted["status"], "STAGE_GREEN")
                self.assertEqual(persisted["pending_manual_ack"], acknowledgement)
                raise RuntimeError("simulated acknowledgement crash")

            with patch(
                "storyloop.stage_lifecycle.consume_manual_ack",
                side_effect=observe_green_then_crash,
            ), self.assertRaisesRegex(RuntimeError, "acknowledgement crash"):
                run_persisted_attempt(
                    project, config, "foundation", {},
                    attempt_runner=lambda *args, **kwargs: result,
                    reserve_ms=4_000,
                    wall_clock_ms=lambda: next(wall_values),
                    monotonic_ms=lambda: next(monotonic_values),
                )
            persisted = load_stage_state(project, config, "foundation", {})
            self.assertEqual(persisted["pending_manual_ack"], acknowledgement)

            with patch("storyloop.stage_lifecycle.consume_manual_ack") as consume:
                recovered = run_persisted_attempt(
                    project, config, "foundation", {},
                    attempt_runner=lambda *args, **kwargs: self.fail(
                        "Green recovery must not rerun the Agent"
                    ),
                    wall_clock_ms=lambda: 2_000, monotonic_ms=lambda: 0,
                )
            consume.assert_called_once_with(project.resolve(), acknowledgement)
            self.assertIsNone(recovered["pending_manual_ack"])
            self.assertIsNone(load_stage_state(
                project, config, "foundation", {},
            )["pending_manual_ack"])

    def test_persisted_runner_maps_agent_nonterminal_without_backoff(self):
        config = loop_config()
        config["budgets"]["total_seconds"] = 10
        wall_values = iter([1_000, 1_100])
        monotonic_values = iter([0, 100])

        def needs_input(*args, **kwargs):
            raise StoryLoopError(
                "private question", code="NEEDS_INPUT",
                details={"questions": ["private choice"]},
            )

        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder) / "project"
            project.mkdir()
            state = run_persisted_attempt(
                project, config, "foundation", {}, attempt_runner=needs_input,
                reserve_ms=4_000, wall_clock_ms=lambda: next(wall_values),
                monotonic_ms=lambda: next(monotonic_values),
            )
            loaded = load_stage_state(project, config, "foundation", {})

        self.assertEqual(state["status"], "NEEDS_INPUT")
        self.assertEqual(loaded, state)
        self.assertEqual(state["attempts_started"], 0)
        self.assertNotIn("private", str(state))

    def test_attempt_is_persisted_before_execution_and_crash_is_recovered(self):
        config = loop_config()
        config["budgets"]["total_seconds"] = 10
        payload = {"goal": "implement foundation"}
        observed = []

        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder) / "project"
            project.mkdir()

            def crash_after_observing_state(*args, **kwargs):
                observed.append(load_stage_state(project, config, "foundation", payload))
                raise RuntimeError("simulated crash")

            with self.assertRaisesRegex(RuntimeError, "simulated crash"):
                run_persisted_attempt(
                    project, config, "foundation", payload,
                    attempt_runner=crash_after_observing_state,
                    reserve_ms=4_000, wall_clock_ms=lambda: 1_000,
                    monotonic_ms=lambda: 0,
                )
            persisted = load_stage_state(project, config, "foundation", payload)
            self.assertEqual(observed[0]["status"], "RUNNING")
            self.assertEqual(persisted["reserved_ms"], 4_000)

            def must_not_run(*args, **kwargs):
                self.fail("Interrupted recovery must stop before another attempt")

            recovered = run_persisted_attempt(
                project, config, "foundation", payload,
                attempt_runner=must_not_run, reserve_ms=1_000,
                wall_clock_ms=lambda: 2_000, monotonic_ms=lambda: 0,
            )
            self.assertEqual(recovered["status"], "BACKOFF")
            self.assertEqual(recovered["consumed_ms"], 4_000)
            self.assertEqual(recovered["next_eligible_at_ms"], 4_000)

    def test_success_rejects_internally_inconsistent_judge_evidence(self):
        config = loop_config()
        for field, value in (("phase", "red"), ("outcome", "RED"), ("exit_code", 1)):
            with self.subTest(field=field):
                state = new_stage_state(Path.cwd(), config, "foundation", {})
                state = begin_attempt(state, config, now_ms=1_000, reserve_ms=4_000, workspace_digest="a" * 64)
                result = green_result()
                result["l1"][0][field] = value
                with self.assertRaises(StoryLoopError):
                    settle_attempt_success(
                        state, config, result, elapsed_ms=100, now_ms=1_100,
                    )

    def test_success_reconciles_time_and_persists_only_safe_evidence(self):
        config = loop_config()
        config["budgets"]["total_seconds"] = 10
        state = new_stage_state(Path.cwd(), config, "foundation", {})
        state = begin_attempt(state, config, now_ms=1_000, reserve_ms=4_000, workspace_digest="a" * 64)
        acknowledgement = {
            "protocol": "loop.manual.ack.v2", "stage": "foundation",
            "role": "implement", "request_id": "9" * 64,
        }
        result = green_result({
            "summary": "synthetic-private-value",
            "_manual_ack": acknowledgement,
        })
        result["changes"] = [
            {"path": "service/business/feature.py", "kind": "added"},
        ]
        settled = settle_attempt_success(
            state, config, result, elapsed_ms=750, now_ms=1_750,
        )

        self.assertEqual(settled["status"], "STAGE_GREEN")
        self.assertEqual(settled["consumed_ms"], 750)
        self.assertEqual(settled["reserved_ms"], 0)
        self.assertEqual(settled["pending_manual_ack"]["request_id"], "9" * 64)
        serialized = str(settled["result"])
        self.assertNotIn("synthetic-private-value", serialized)
        self.assertIn("service/business/feature.py", serialized)

if __name__ == "__main__":
    unittest.main()
