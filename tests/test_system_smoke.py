"""Layer 1: the project-level system smoke suite protects core flows across every change."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from storyloop.approvals import approve_change
from storyloop.baseline import load_project_config
from storyloop.onboarding_cli import bootstrap_with_project_checks as bootstrap_project
from tests.routing_support import legacy_create_change as create_change
from storyloop.models import StoryLoopError
from storyloop.project_boundaries import audit_project_changes
from storyloop.project_loop import LoopServices, load_run_state, run_change
from storyloop.ci_command import run_ci
from storyloop.system_smoke import (SYSTEM_SMOKE_DIR, install_system_smoke, load_system_smoke,
                                    run_system_smoke_isolated)
from tests.support import product
from tests.test_baseline_behavior import git, repository
from tests.test_project_loop import smoke_payload

NOW = datetime(2026, 9, 23, tzinfo=timezone.utc)


def _suite(exit_code: int) -> tuple[list[dict], dict]:
    files = [{"path": "core_login.py", "content": f"raise SystemExit({exit_code})\n"}]
    manifest = {"version": 1, "tests": [{"id": "core-login", "description": "登录并打开首页",
                                         "command": ["{python}", f"{SYSTEM_SMOKE_DIR}/core_login.py"],
                                         "timeout_seconds": 30}]}
    return files, manifest


class SystemSmokeSuiteTests(unittest.TestCase):
    def test_init_preflights_committed_system_manifest_before_control_writes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            manifest = root / SYSTEM_SMOKE_DIR / "manifest.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text('{"version": 1, "tests": []}\n', encoding="utf-8")
            git(root, "add", ".")
            git(root, "commit", "-q", "-m", "invalid smoke")
            with patch("storyloop.system_smoke.adopt_system_smoke") as adopt:
                with self.assertRaisesRegex(StoryLoopError, "System smoke manifest needs tests"):
                    bootstrap_project(root, {"unit_commands": []})
                adopt.assert_not_called()
            self.assertFalse((root / ".storyloop").exists())

    def test_install_protects_the_suite_and_ignores_legacy_change_directories(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            (root / SYSTEM_SMOKE_DIR / "FEAT-20260916-001").mkdir(parents=True)
            (root / SYSTEM_SMOKE_DIR / "FEAT-20260916-001/old.py").write_text("pass\n", encoding="utf-8")
            git(root, "add", "."); git(root, "commit", "-q", "-m", "legacy")
            bootstrap_project(root, {"unit_commands": []})
            self.assertIsNone(load_system_smoke(root))
            files, manifest = _suite(0)
            installed = install_system_smoke(root, files, manifest)
            self.assertEqual(set(installed["digests"]), {"manifest.json", "core_login.py"})
            self.assertEqual(load_system_smoke(root)["tests"][0]["id"], "core-login")
            (root / SYSTEM_SMOKE_DIR / "core_login.py").write_text("raise SystemExit(1)\n", encoding="utf-8")
            with self.assertRaisesRegex(StoryLoopError, "modified"):
                load_system_smoke(root)

    def test_isolated_run_reports_pass_and_fail(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            bootstrap_project(root, {"unit_commands": []})
            self.assertEqual(run_system_smoke_isolated(root)["status"], "SKIPPED")
            install_system_smoke(root, *_suite(0))
            git(root, "add", "."); git(root, "commit", "-q", "-m", "smoke")
            self.assertEqual(run_system_smoke_isolated(root)["status"], "PASSED")
            install_system_smoke(root, *_suite(1))
            git(root, "add", "."); git(root, "commit", "-q", "-m", "smoke red")
            result = run_system_smoke_isolated(root)
            self.assertEqual(result["status"], "FAILED")
            self.assertEqual(result["results"][0]["id"], "core-login")


class SystemSmokeInTheLoopTests(unittest.TestCase):
    def _approved(self, exit_code: int):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        repository(root)
        bootstrap_project(root, {"unit_commands": []})
        install_system_smoke(root, *_suite(exit_code))
        git(root, "add", "."); git(root, "commit", "-q", "-m", "system smoke")
        state = create_change(root, "orders", product(), now=lambda: NOW)
        approve_change(root, state["change_id"], now=lambda: NOW)
        return temporary, root, state["change_id"]

    def _services(self, change_id: str, implement):
        return LoopServices(
            generate_smoke=lambda project, state: smoke_payload(change_id),
            implement=implement,
            review=lambda project, context: {"verdict": "pass", "summary": "ok", "findings": [], "questions": []},
        )

    def _implement(self, project, context):
        target = project / "modules/orders/service.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("VALUE = 1\n", encoding="utf-8")
        return {"summary": "done", "questions": []}

    def test_a_change_that_breaks_the_system_smoke_cannot_pass(self):
        temporary, root, change_id = self._approved(exit_code=1)
        try:
            config = load_project_config(root)
            config["max_attempts"] = 1
            (root / ".storyloop/config.json").write_text(json.dumps(config), encoding="utf-8")
            result = run_change(root, change_id, self._services(change_id, self._implement))
            self.assertEqual(result["status"], "BUDGET_EXHAUSTED")
            failure = load_run_state(root, change_id)["failures"][-1]
            self.assertEqual(failure["smoke"]["status"], "FAILED")
            self.assertEqual(failure["acceptance"]["status"], "PASSED")
        finally:
            temporary.cleanup()

    def test_a_green_system_smoke_is_reported_and_ci_runs_both_layers(self):
        temporary, root, change_id = self._approved(exit_code=0)
        try:
            result = run_change(root, change_id, self._services(change_id, self._implement))
            self.assertEqual(result["status"], "PASSED")
            report = json.loads(Path(result["report"]).read_text(encoding="utf-8"))
            self.assertEqual(report["layers"]["smoke"]["status"], "PASSED")
            ci = run_ci(root, change_id)
            self.assertEqual(ci["status"], "PASSED")
            self.assertEqual(ci["smoke"]["status"], "PASSED")
            self.assertEqual(ci["acceptance"]["status"], "PASSED")
        finally:
            temporary.cleanup()

    def test_feature_changes_may_not_touch_the_system_smoke_suite(self):
        temporary, root, change_id = self._approved(exit_code=0)
        try:
            def implement(project, context):
                self._implement(project, context)
                (project / SYSTEM_SMOKE_DIR / "core_login.py").write_text("raise SystemExit(0)  # edited\n",
                                                                           encoding="utf-8")
                return {"summary": "touched smoke", "questions": []}

            result = run_change(root, change_id, self._services(change_id, implement))
            self.assertEqual(result["status"], "BOUNDARY_VIOLATION")
            audit = audit_project_changes(root, change_id)
            categories = {item["path"]: item["category"] for item in audit["violations"]}
            self.assertEqual(categories[f"{SYSTEM_SMOKE_DIR}/core_login.py"], "SMOKE_PROTECTED")
        finally:
            temporary.cleanup()


if __name__ == "__main__":
    unittest.main()
