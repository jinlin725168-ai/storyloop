"""Unit, integration and static checks are separate layers: separate commands, separate verdicts."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest

from storyloop.approvals import approve_change
from storyloop.baseline import bootstrap_project, load_project_config
from storyloop.changes import create_change
from storyloop.models import StoryLoopError
from storyloop.project_loop import LoopServices, load_run_state, run_change
from storyloop.project_profile import layer_commands, validate_project_settings
from storyloop.storage import load_json
from tests.support import product
from tests.test_baseline_behavior import git, repository
from tests.test_project_loop import smoke_payload

NOW = datetime(2026, 9, 23, tzinfo=timezone.utc)
PASS = [sys.executable, "-c", "pass"]
FAIL = [sys.executable, "-c", "raise SystemExit(1)"]


class LayerSettingsTests(unittest.TestCase):
    def test_settings_declare_each_layer_separately(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            profile = validate_project_settings(root, {
                "unit_commands": [PASS], "integration_commands": [FAIL], "check_commands": [PASS],
                "test_roots": ["tests", "src/**/*.test.ts"],
            })
            self.assertEqual(layer_commands(profile, "unit"), [PASS])
            self.assertEqual(layer_commands(profile, "integration"), [FAIL])
            self.assertEqual(layer_commands(profile, "checks"), [PASS])
            self.assertEqual(profile["test_roots"], ["src/**/*.test.ts", "tests"])

    def test_legacy_test_commands_mean_unit_and_other_layers_are_empty(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            profile = validate_project_settings(root, {"test_commands": [PASS]})
            self.assertEqual(layer_commands(profile, "unit"), [PASS])
            self.assertEqual(layer_commands(profile, "integration"), [])
            self.assertEqual(layer_commands(profile, "checks"), [])
            # A config written by an older engine keeps only test_commands.
            self.assertEqual(layer_commands({"test_commands": [FAIL]}, "unit"), [FAIL])
            with self.assertRaises(StoryLoopError):
                validate_project_settings(root, {"unit_commands": [PASS], "test_commands": [PASS]})


class LayeredLoopTests(unittest.TestCase):
    def _approved(self, settings: dict):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        repository(root)
        bootstrap_project(root, settings)
        state = create_change(root, "orders", product(), now=lambda: NOW)
        approve_change(root, state["change_id"], now=lambda: NOW)
        return temporary, root, state["change_id"]

    def _services(self, change_id: str, implement):
        return LoopServices(
            generate_smoke=lambda project, state: smoke_payload(change_id),
            implement=implement,
            review=lambda project, context: {"verdict": "pass", "summary": "ok", "findings": [], "questions": []},
        )

    def _good_implement(self, project, context):
        target = project / "modules/orders/service.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("VALUE = 1\n", encoding="utf-8")
        return {"summary": "done", "questions": []}

    def test_each_layer_is_verified_and_reported_on_its_own(self):
        temporary, root, change_id = self._approved(
            {"unit_commands": [PASS], "integration_commands": [PASS], "check_commands": [PASS]})
        try:
            result = run_change(root, change_id, self._services(change_id, self._good_implement))
            self.assertEqual(result["status"], "PASSED")
            report = load_json(Path(result["report"]))
            layers = report["layers"]
            self.assertEqual({k: v["status"] for k, v in layers.items()},
                             {"unit": "PASSED", "integration": "PASSED", "acceptance": "PASSED",
                              "smoke": "SKIPPED", "checks": "PASSED"})
            markdown = Path(result["report"]).with_suffix(".md").read_text(encoding="utf-8")
            self.assertIn("分层验证", markdown)
            self.assertIn("integration", markdown)
        finally:
            temporary.cleanup()

    def test_a_failing_integration_layer_fails_the_attempt_even_when_unit_passes(self):
        temporary, root, change_id = self._approved(
            {"unit_commands": [PASS], "integration_commands": [FAIL]})
        try:
            config = load_project_config(root)
            config["max_attempts"] = 1
            (root / ".storyloop/config.json").write_text(json.dumps(config), encoding="utf-8")
            result = run_change(root, change_id, self._services(change_id, self._good_implement))
            self.assertEqual(result["status"], "BUDGET_EXHAUSTED")
            failure = load_run_state(root, change_id)["failures"][-1]
            self.assertEqual(failure["unit"]["status"], "PASSED")
            self.assertEqual(failure["integration"]["status"], "FAILED")
            self.assertEqual(failure["acceptance"]["status"], "PASSED")
        finally:
            temporary.cleanup()

    def test_a_failing_static_check_fails_the_attempt(self):
        temporary, root, change_id = self._approved({"unit_commands": [], "check_commands": [FAIL]})
        try:
            config = load_project_config(root)
            config["max_attempts"] = 1
            (root / ".storyloop/config.json").write_text(json.dumps(config), encoding="utf-8")
            result = run_change(root, change_id, self._services(change_id, self._good_implement))
            self.assertEqual(result["status"], "BUDGET_EXHAUSTED")
            failure = load_run_state(root, change_id)["failures"][-1]
            self.assertEqual(failure["checks"]["status"], "FAILED")
            self.assertEqual(failure["unit"]["status"], "SKIPPED")
        finally:
            temporary.cleanup()


if __name__ == "__main__":
    unittest.main()
