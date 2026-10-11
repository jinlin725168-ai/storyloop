from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from storyloop.approvals import approve_change
from storyloop.baseline import bootstrap_project
from storyloop.changes import create_change
from storyloop.models import StoryLoopError
from storyloop.project_loop import LoopServices, run_change
from tests.support import product
from tests.test_baseline_behavior import git, repository
from tests.test_project_loop import smoke_payload


class ProjectLoopResumeTests(unittest.TestCase):
    def test_restored_base_violation_resumes_existing_feature_branch(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            (root / "core.py").write_text("STABLE = True\n", encoding="utf-8")
            git(root, "add", "core.py")
            git(root, "commit", "-q", "-m", "core")
            bootstrap_project(root, {"test_commands": []})
            state = create_change(
                root, "orders", product(),
                now=lambda: datetime(2026, 9, 16, tzinfo=timezone.utc),
            )
            change_id = state["change_id"]
            approve_change(root, change_id)

            def base_change(project, context):
                (project / "core.py").write_text("STABLE = False\n", encoding="utf-8")
                return {"summary": "base needed", "questions": []}

            first = run_change(root, change_id, LoopServices(
                generate_smoke=lambda project, change: smoke_payload(change_id),
                implement=base_change,
                review=lambda project, context: {
                    "verdict": "pass", "summary": "ok", "findings": [], "questions": [],
                },
            ))
            self.assertEqual(first["status"], "BASE_CHANGE_REQUIRED")
            (root / "core.py").write_text("STABLE = True\n", encoding="utf-8")

            def feature_change(project, context):
                path = project / "modules/orders/service.py"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("VALUE = 1\n", encoding="utf-8")
                return {"summary": "implemented", "questions": []}

            second = run_change(root, change_id, LoopServices(
                generate_smoke=lambda project, change: self.fail("smoke must not regenerate"),
                implement=feature_change,
                review=lambda project, context: {
                    "verdict": "pass", "summary": "ok", "findings": [], "questions": [],
                },
            ))

            self.assertEqual(second["status"], "PASSED")
            self.assertEqual(second["attempts"], 2)

    def test_finalization_resumes_after_change_status_write_crash(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            bootstrap_project(root, {"test_commands": []})
            state = create_change(
                root, "orders", product(),
                now=lambda: datetime(2026, 9, 16, tzinfo=timezone.utc),
            )
            change_id = state["change_id"]
            approve_change(root, change_id)

            def implement(project, context):
                path = project / "modules/orders/service.py"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("VALUE = 1\n", encoding="utf-8")
                return {"summary": "implemented", "questions": []}

            services = LoopServices(
                generate_smoke=lambda project, change: smoke_payload(change_id),
                implement=implement,
                review=lambda project, context: {
                    "verdict": "pass", "summary": "ok", "findings": [], "questions": [],
                },
            )
            with patch("storyloop.project_loop_finish.set_change_status",
                       side_effect=StoryLoopError("simulated finalization crash")):
                with self.assertRaisesRegex(StoryLoopError, "simulated finalization"):
                    run_change(root, change_id, services)

            result = run_change(root, change_id, services)
            self.assertEqual(result["status"], "PASSED")


if __name__ == "__main__":
    unittest.main()
