"""The last two stops the coverage manifest listed as gaps (roadmap D1)."""
import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from storyloop.approvals import approve_change
from storyloop.baseline import bootstrap_project
from storyloop.models import StoryLoopError
from storyloop.project_loop import load_run_state, run_change
from storyloop.run_hints import next_step
from tests.routing_support import legacy_create_change
from tests.support import product
from tests.test_baseline_behavior import repository
from tests.test_project_loop import smoke_payload
from tests.test_smoke_repair import services


class StopReachabilityTests(unittest.TestCase):
    def test_red_that_cannot_execute_stops_as_red_environment_error_with_a_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            bootstrap_project(root, {"test_commands": []})
            change_id = legacy_create_change(root, "orders", product())["change_id"]
            approve_change(root, change_id)
            error = StoryLoopError("RED commands could not start", code="BLOCKED",
                                   details={"results": [{"id": "SMOKE-1", "status": "ERROR"}]})
            with patch("storyloop.project_preparation._ensure_red_evidence", side_effect=error):
                result = run_change(root, change_id, services(change_id, lambda *_: smoke_payload(change_id)))
            state = load_run_state(root, change_id)
            self.assertEqual(result["status"], "NEEDS_INPUT")
            self.assertEqual(state["acceptance"]["reason"], "RED_ENVIRONMENT_ERROR")
            self.assertIn("修复 RED 执行环境后重试", state["next"])

    def test_an_interrupted_command_exits_130_and_points_to_run(self):
        from storyloop.cli import main
        with tempfile.TemporaryDirectory() as folder:
            errors = io.StringIO()
            with patch("storyloop.cli.dispatch", side_effect=KeyboardInterrupt), \
                    contextlib.redirect_stderr(errors):
                code = main(["run", folder, "--change", "FEAT-20260101-001"])
        self.assertEqual(code, 130)
        self.assertIn("Resume with run", errors.getvalue())
        self.assertIn("storyloop run", next_step("INTERRUPTED", "FEAT-20260101-001", "/p"))


if __name__ == "__main__":
    unittest.main()
