"""Cross-run recovery tests for the v2 Stage lifecycle."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from storyloop.models import StoryLoopError
from storyloop.stage_agents import adapter_control_root, invoke_selected_adapter
from storyloop.stage_lifecycle import run_persisted_attempt
from storyloop.stage_state import stage_state_path
from tests.test_stage_config import loop_config
from tests.test_stage_runner import judge_evidence


def green_result() -> dict:
    return {
        "stage": "foundation", "outcome": "STAGE_GREEN",
        "agent_invoked": True, "agent_response": {"status": "ok"},
        "changes": [],
        "red": judge_evidence("red", "red", "RED"),
        "l1": [judge_evidence("core", "l1", "GREEN")],
        "l2": [judge_evidence("full", "l2", "GREEN")],
    }


class StageLifecycleTests(unittest.TestCase):
    def test_lifecycle_passes_one_monotonic_deadline_to_the_attempt(self):
        config = loop_config()
        captured = []
        mono_values = iter([500, 600])

        def attempt(*args, **kwargs):
            captured.append((kwargs.get("attempt_deadline_ms"), kwargs.get("budget_clock_ms")))
            return green_result()

        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder) / "project"
            project.mkdir()
            wall_values = iter([1_000, 1_100])
            state = run_persisted_attempt(
                project, config, "foundation", {}, attempt_runner=attempt,
                reserve_ms=1_000,
                wall_clock_ms=lambda: next(wall_values),
                monotonic_ms=lambda: next(mono_values),
            )
        self.assertEqual(state["status"], "STAGE_GREEN")
        self.assertEqual(captured, [(1_500, captured[0][1])])
        self.assertTrue(callable(captured[0][1]))

    def test_attempt_overrun_is_fully_charged_and_never_left_running(self):
        config = loop_config()
        config["budgets"]["total_seconds"] = 10
        wall_values = iter([1_000, 2_500])
        mono_values = iter([0, 1_500])
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder) / "project"
            project.mkdir()
            state = run_persisted_attempt(
                project, config, "foundation", {},
                attempt_runner=lambda *args, **kwargs: green_result(),
                reserve_ms=1_000,
                wall_clock_ms=lambda: next(wall_values),
                monotonic_ms=lambda: next(mono_values),
            )
        self.assertEqual(state["status"], "BACKOFF")
        self.assertEqual(state["consumed_ms"], 1_500)
        self.assertEqual(state["reserved_ms"], 0)

    def test_workspace_taint_is_not_automatically_retried(self):
        config = loop_config()
        wall_values = iter([1_000, 1_100])
        mono_values = iter([0, 100])
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder) / "project"
            project.mkdir()
            state = run_persisted_attempt(
                project, config, "foundation", {},
                attempt_runner=lambda *args, **kwargs: (_ for _ in ()).throw(
                    StoryLoopError("boundary changed", code="WORKSPACE_TAINTED")
                ),
                reserve_ms=1_000,
                wall_clock_ms=lambda: next(wall_values),
                monotonic_ms=lambda: next(mono_values),
            )
            blocked = run_persisted_attempt(
                project, config, "foundation", {},
                attempt_runner=lambda *args, **kwargs: self.fail(
                    "Tainted workspace must not retry automatically"
                ),
                wall_clock_ms=lambda: 1_200, monotonic_ms=lambda: 0,
            )
        self.assertEqual(state["status"], "WORKSPACE_TAINTED")
        self.assertEqual(blocked["status"], "WORKSPACE_TAINTED")

    def test_crash_with_workspace_changes_recovers_as_tainted(self):
        config = loop_config()
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder) / "project"
            (project / "acceptance").mkdir(parents=True)

            def change_then_crash(*args, **kwargs):
                (project / "acceptance" / "tainted.py").write_text(
                    "changed", encoding="utf-8",
                )
                raise RuntimeError("simulated crash")

            with self.assertRaisesRegex(RuntimeError, "simulated crash"):
                run_persisted_attempt(
                    project, config, "foundation", {},
                    attempt_runner=change_then_crash, reserve_ms=1_000,
                    wall_clock_ms=lambda: 1_000, monotonic_ms=lambda: 0,
                )
            recovered = run_persisted_attempt(
                project, config, "foundation", {},
                attempt_runner=lambda *args, **kwargs: self.fail(
                    "Changed crash recovery must not run another attempt"
                ),
                wall_clock_ms=lambda: 2_000, monotonic_ms=lambda: 0,
            )
        self.assertEqual(recovered["status"], "WORKSPACE_TAINTED")
        self.assertEqual(recovered["consumed_ms"], 1_000)

    def test_tainted_lifecycle_requires_the_project_to_be_restored(self):
        config = loop_config()
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder) / "project"
            project.mkdir()
            readme = project / "README.md"
            readme.write_text("baseline", encoding="utf-8")

            def taint(*args, **kwargs):
                readme.write_text("polluted", encoding="utf-8")
                raise StoryLoopError("changed", code="WORKSPACE_TAINTED")

            first_wall = iter([1_000, 1_100])
            first_mono = iter([0, 100])
            state = run_persisted_attempt(
                project, config, "foundation", {}, attempt_runner=taint,
                reserve_ms=1_000, wall_clock_ms=lambda: next(first_wall),
                monotonic_ms=lambda: next(first_mono),
            )
            with self.assertRaisesRegex(StoryLoopError, "(?i)restore"):
                run_persisted_attempt(
                    project, config, "foundation", {}, resume_waiting=True,
                    acknowledge_unsafe=True,
                    attempt_runner=lambda *args, **kwargs: self.fail("must not run"),
                )
            self.assertEqual(state["status"], "WORKSPACE_TAINTED")
            readme.write_text("baseline", encoding="utf-8")
            second_wall = iter([1_200, 1_300])
            second_mono = iter([0, 100])
            recovered = run_persisted_attempt(
                project, config, "foundation", {}, resume_waiting=True,
                acknowledge_unsafe=True,
                attempt_runner=lambda *args, **kwargs: green_result(),
                reserve_ms=1_000, wall_clock_ms=lambda: next(second_wall),
                monotonic_ms=lambda: next(second_mono),
            )
        self.assertEqual(recovered["status"], "STAGE_GREEN")
    def test_uninspectable_crash_recovery_is_persisted_as_tainted(self):
        config = loop_config()
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder) / "project"
            project.mkdir()

            def crash(*args, **kwargs):
                (project / ".env").write_text("not-a-secret", encoding="utf-8")
                raise RuntimeError("simulated crash")

            with self.assertRaisesRegex(RuntimeError, "simulated crash"):
                run_persisted_attempt(
                    project, config, "foundation", {}, attempt_runner=crash,
                    reserve_ms=1_000, wall_clock_ms=lambda: 1_000,
                    monotonic_ms=lambda: 0,
                )
            recovered = run_persisted_attempt(
                project, config, "foundation", {},
                attempt_runner=lambda *args, **kwargs: self.fail("must not run"),
                wall_clock_ms=lambda: 2_000, monotonic_ms=lambda: 0,
            )
            persisted = json.loads(stage_state_path(project, "foundation").read_text())
        self.assertEqual(recovered["status"], "WORKSPACE_TAINTED")
        self.assertEqual(persisted["status"], "WORKSPACE_TAINTED")
        self.assertEqual(recovered["consumed_ms"], 1_000)
    def test_hard_link_after_crash_is_persisted_as_tainted(self):
        config = loop_config()
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder) / "project"
            project.mkdir()
            source = project / "source.txt"
            source.write_text("baseline", encoding="utf-8")

            def crash(*args, **kwargs):
                os.link(source, project / "linked.txt")
                raise RuntimeError("simulated crash")

            with self.assertRaisesRegex(RuntimeError, "simulated crash"):
                run_persisted_attempt(
                    project, config, "foundation", {}, attempt_runner=crash,
                    reserve_ms=1_000, wall_clock_ms=lambda: 1_000,
                    monotonic_ms=lambda: 0,
                )
            recovered = run_persisted_attempt(
                project, config, "foundation", {},
                wall_clock_ms=lambda: 2_000, monotonic_ms=lambda: 0,
            )
        self.assertEqual(recovered["status"], "WORKSPACE_TAINTED")

    def test_symbolic_link_after_crash_is_persisted_as_tainted(self):
        config = loop_config()
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder) / "project"
            project.mkdir()
            target = project / "target.txt"
            target.write_text("baseline", encoding="utf-8")
            probe = project / "probe-link"
            try:
                probe.symlink_to(target.name)
                probe.unlink()
            except OSError:
                self.skipTest("symbolic links are unavailable")

            def crash(*args, **kwargs):
                (project / "linked.txt").symlink_to(target.name)
                raise RuntimeError("simulated crash")

            with self.assertRaisesRegex(RuntimeError, "simulated crash"):
                run_persisted_attempt(
                    project, config, "foundation", {}, attempt_runner=crash,
                    reserve_ms=1_000, wall_clock_ms=lambda: 1_000,
                    monotonic_ms=lambda: 0,
                )
            recovered = run_persisted_attempt(
                project, config, "foundation", {},
                wall_clock_ms=lambda: 2_000, monotonic_ms=lambda: 0,
            )
        self.assertEqual(recovered["status"], "WORKSPACE_TAINTED")
    def test_unsafe_judge_containment_requires_explicit_safe_resume(self):
        config = loop_config()
        unsafe = {
            "returncode": None, "stdout": "", "stderr": "",
            "timed_out": False, "truncated": False, "error": "",
            "contained": False,
        }
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder) / "project"
            project.mkdir()
            wall_values = iter([1_000, 1_100])
            with patch("storyloop.stage_judges.run_process", return_value=unsafe):
                state = run_persisted_attempt(
                    project, config, "foundation", {},
                    resolver=lambda name: "C:/python.exe", reserve_ms=1_000,
                    wall_clock_ms=lambda: next(wall_values),
                    monotonic_ms=lambda: 0,
                )
            blocked = run_persisted_attempt(
                project, config, "foundation", {},
                attempt_runner=lambda *args, **kwargs: self.fail("must not retry"),
            )
            self.assertEqual(state["status"], "UNSAFE_CONTAINMENT")
            self.assertIsNotNone(state["attempt_workspace_digest"])
            self.assertEqual(blocked["status"], "UNSAFE_CONTAINMENT")
            resume_wall = iter([1_200, 1_300])
            resumed = run_persisted_attempt(
                project, config, "foundation", {}, resume_waiting=True,
                acknowledge_unsafe=True,
                attempt_runner=lambda *args, **kwargs: green_result(),
                reserve_ms=1_000, wall_clock_ms=lambda: next(resume_wall),
                monotonic_ms=lambda: 0,
            )
        self.assertEqual(resumed["status"], "STAGE_GREEN")
    def test_nonmanual_green_does_not_consume_a_prior_manual_response(self):
        config = loop_config()
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder) / "project"
            project.mkdir()
            with self.assertRaises(StoryLoopError):
                invoke_selected_adapter(
                    project, config, "implement", "foundation", {}, requested="manual",
                )
            root = adapter_control_root(project)
            request = json.loads((
                root / "requests" / "foundation.implement.json"
            ).read_text(encoding="utf-8"))
            response_path = root / "responses" / "foundation.implement.json"
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
            mono_values = iter([0, 100])
            state = run_persisted_attempt(
                project, config, "foundation", {},
                attempt_runner=lambda *args, **kwargs: result,
                reserve_ms=1_000,
                wall_clock_ms=lambda: next(wall_values),
                monotonic_ms=lambda: next(mono_values),
            )

            self.assertEqual(state["status"], "STAGE_GREEN")
            self.assertTrue(response_path.exists())

    def test_deleted_state_cannot_reset_an_initialized_stage_budget(self):
        config = loop_config()
        config["budgets"]["total_seconds"] = 10
        wall_values = iter([1_000, 1_100])
        monotonic_values = iter([0, 100])

        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder) / "project"
            project.mkdir()
            failed = run_persisted_attempt(
                project, config, "foundation", {},
                attempt_runner=lambda *args, **kwargs: (_ for _ in ()).throw(
                    StoryLoopError("failed", code="JUDGE_FAILED")
                ),
                reserve_ms=1_000,
                wall_clock_ms=lambda: next(wall_values),
                monotonic_ms=lambda: next(monotonic_values),
            )
            stage_state_path(project, "foundation").unlink()
            with self.assertRaisesRegex(StoryLoopError, "missing"):
                run_persisted_attempt(
                    project, config, "foundation", {},
                    attempt_runner=lambda *args, **kwargs: self.fail(
                        "Deleted state must not start a fresh attempt"
                    ),
                    reserve_ms=1_000,
                    wall_clock_ms=lambda: 2_000, monotonic_ms=lambda: 0,
                )

        self.assertEqual(failed["attempts_started"], 1)

    def test_explicit_waiting_resume_can_rebind_a_new_payload(self):
        config = loop_config()
        config["budgets"]["total_seconds"] = 10
        first_wall = iter([1_000, 1_100])
        first_mono = iter([0, 100])

        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder) / "project"
            project.mkdir()
            waiting = run_persisted_attempt(
                project, config, "foundation", {"question": "old"},
                attempt_runner=lambda *args, **kwargs: (_ for _ in ()).throw(
                    StoryLoopError("answer required", code="NEEDS_INPUT")
                ),
                reserve_ms=1_000,
                wall_clock_ms=lambda: next(first_wall),
                monotonic_ms=lambda: next(first_mono),
            )
            second_wall = iter([1_200, 1_300])
            second_mono = iter([0, 100])
            resumed = run_persisted_attempt(
                project, config, "foundation", {"answer": "new"},
                attempt_runner=lambda *args, **kwargs: green_result(),
                reserve_ms=1_000, resume_waiting=True,
                wall_clock_ms=lambda: next(second_wall),
                monotonic_ms=lambda: next(second_mono),
            )

        self.assertEqual(waiting["status"], "NEEDS_INPUT")
        self.assertEqual(resumed["status"], "STAGE_GREEN")


if __name__ == "__main__":
    unittest.main()
