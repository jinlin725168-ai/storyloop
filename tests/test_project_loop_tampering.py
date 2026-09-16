import json
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from storyloop.approvals import approve_change
from storyloop.baseline import bootstrap_project
from storyloop.changes import create_change
from storyloop.models import StoryLoopError
from storyloop.project_loop import LoopServices, run_change
from storyloop.storage import write_json
from tests.support import product
from tests.test_baseline_behavior import repository
from tests.test_project_loop import smoke_payload


class ProjectLoopTamperingTests(unittest.TestCase):
    def _approved(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        repository(root)
        bootstrap_project(root, {"test_commands": []})
        state = create_change(
            root, "orders", product(),
            now=lambda: datetime(2026, 9, 16, tzinfo=timezone.utc),
        )
        approve_change(root, state["change_id"])
        return temporary, root, state["change_id"]

    def test_implementation_cannot_modify_ignored_control_plane(self):
        temporary, root, change_id = self._approved()

        def implement(project, context):
            config_path = project / ".storyloop/config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["max_attempts"] = 999
            write_json(config_path, config)
            return {"summary": "tampered", "questions": []}

        try:
            with self.assertRaisesRegex(StoryLoopError, "control plane"):
                run_change(root, change_id, LoopServices(
                    generate_smoke=lambda project, change: smoke_payload(change_id),
                    implement=implement,
                    review=lambda project, context: {
                        "verdict": "pass", "summary": "ok", "findings": [], "questions": [],
                    },
                ))
        finally:
            temporary.cleanup()

    def test_review_cannot_change_code_after_green_evidence(self):
        temporary, root, change_id = self._approved()

        def implement(project, context):
            path = project / "modules/orders/service.py"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("VALUE = 1\n", encoding="utf-8")
            return {"summary": "implemented", "questions": []}

        def review(project, context):
            (project / "modules/orders/service.py").write_text("VALUE = 2\n", encoding="utf-8")
            return {"verdict": "pass", "summary": "tampered", "findings": [], "questions": []}

        try:
            with self.assertRaisesRegex(StoryLoopError, "review|workspace|project"):
                run_change(root, change_id, LoopServices(
                    generate_smoke=lambda project, change: smoke_payload(change_id),
                    implement=implement,
                    review=review,
                ))
        finally:
            temporary.cleanup()

    def test_review_cannot_change_tracked_code_in_a_cache_named_directory(self):
        temporary, root, change_id = self._approved()

        def implement(project, context):
            service = project / "modules/orders/service.py"
            hidden = project / "modules/orders/target/runtime.py"
            hidden.parent.mkdir(parents=True, exist_ok=True)
            service.write_text("VALUE = 1\n", encoding="utf-8")
            hidden.write_text("VALUE = 1\n", encoding="utf-8")
            return {"summary": "implemented", "questions": []}

        def review(project, context):
            target = project / "modules/orders/target/runtime.py"
            target.write_text("VALUE = 2\n", encoding="utf-8")
            return {"verdict": "pass", "summary": "tampered", "findings": [], "questions": []}

        try:
            with self.assertRaisesRegex(StoryLoopError, "modified the project"):
                run_change(root, change_id, LoopServices(
                    generate_smoke=lambda project, change: smoke_payload(change_id),
                    implement=implement,
                    review=review,
                ))
        finally:
            temporary.cleanup()


if __name__ == "__main__":
    unittest.main()
