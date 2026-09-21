"""After review passes, an optional external generator adds UI regression assets under acceptance/ui/."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from storyloop.approvals import approve_change
from storyloop.baseline import bootstrap_project, load_project_config
from storyloop.changes import create_change
from storyloop.models import StoryLoopError
from storyloop.project_loop import LoopServices, load_run_state, run_change
from storyloop.storage import write_json
from storyloop.ui_regression import install_ui_assets
from tests.test_baseline_behavior import repository
from tests.test_project_loop import smoke_payload
from tests.test_product_spec import rich_product

NOW = datetime(2026, 9, 21, tzinfo=timezone.utc)

GENERATOR = '''
import json, sys
request = json.load(sys.stdin)
assert request["role"] == "ui_regression", request
root = request["payload"]["suite_root"]
print(json.dumps({"files": [
    {"path": "plan.md", "content": "# plan for " + request["payload"]["capability"] + "\\n"},
    {"path": "add-supplier.spec.ts", "content": "// generated against " + request["payload"].get("app_hint", "") + "\\n"},
]}))
'''


def _project(root: Path, generator: bool) -> str:
    repository(root)
    bootstrap_project(root, {"test_commands": []})
    if generator:
        script = root / ".storyloop/ui_generator.py"
        script.write_text(GENERATOR, encoding="utf-8")
        config = load_project_config(root)
        config["ui_regression"] = {"command": [sys.executable, str(script)], "timeout_seconds": 60}
        write_json(root / ".storyloop/config.json", config)
    value = rich_product()
    value["questions"] = []
    state = create_change(root, "orders", value, now=lambda: NOW)
    approve_change(root, state["change_id"], now=lambda: NOW)
    return state["change_id"]


def _services(change_id: str) -> LoopServices:
    def implement(project, context):
        target = project / "modules/orders/service.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("VALUE = 1\n", encoding="utf-8")
        return {"summary": "done", "questions": []}

    return LoopServices(generate_smoke=lambda p, s: smoke_payload(change_id), implement=implement,
                        review=lambda p, c: {"verdict": "pass", "summary": "ok", "findings": [], "questions": []})


class UiRegressionTests(unittest.TestCase):
    def test_generated_assets_are_committed_under_acceptance_ui(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            change_id = _project(root, generator=True)
            done = run_change(root, change_id, _services(change_id))
            self.assertEqual(done["status"], "PASSED", done)
            tracked = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True).stdout.split()
            self.assertIn("acceptance/ui/room-booking/plan.md", tracked)
            self.assertIn("acceptance/ui/room-booking/add-supplier.spec.ts", tracked)
            state = load_run_state(root, change_id)
            self.assertEqual(state["ui_regression"]["status"], "GENERATED")
            self.assertEqual(state["ui_regression"]["files"], 2)
            self.assertEqual(subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True,
                                            text=True).stdout.strip(), "")

    def test_without_configuration_nothing_is_generated(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            change_id = _project(root, generator=False)
            done = run_change(root, change_id, _services(change_id))
            self.assertEqual(done["status"], "PASSED", done)
            self.assertFalse((root / "acceptance/ui").exists())
            self.assertEqual(load_run_state(root, change_id)["ui_regression"]["status"], "SKIPPED")

    def test_assets_may_only_land_inside_the_capability_suite_root(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            bootstrap_project(root)
            with self.assertRaises(StoryLoopError):
                install_ui_assets(root, "room-booking", {"files": [{"path": "../../evil.ts", "content": "x"}]})
            with self.assertRaises(StoryLoopError):
                install_ui_assets(root, "room-booking", {"files": [{"path": "ok.spec.ts", "content": 5}]})
            written = install_ui_assets(root, "room-booking", {"files": [{"path": "nested/ok.spec.ts", "content": "x"}]})
            self.assertEqual(written, ["acceptance/ui/room-booking/nested/ok.spec.ts"])


if __name__ == "__main__":
    unittest.main()
