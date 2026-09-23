"""Before implementation, existing behaviour on the paths about to change is locked by tests that are green first."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys
import tempfile
import unittest

from storyloop.approvals import approve_change
from storyloop.baseline import bootstrap_project
from storyloop.changes import create_change
from storyloop.project_loop import LoopServices, load_run_state, run_change
from storyloop.project_profile import matches_test_root
from tests.support import product
from tests.test_baseline_behavior import git, repository
from tests.test_project_loop import smoke_payload

NOW = datetime(2026, 9, 23, tzinfo=timezone.utc)
UNIT = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."]
GREEN_TEST = ("import unittest\nfrom modules.orders import service\n\n\nclass Keep(unittest.TestCase):\n"
              "    def test_value_stays_present(self):\n        self.assertIn('VALUE', dir(service))\n")


def _repo(settings: dict):
    temporary = tempfile.TemporaryDirectory()
    root = Path(temporary.name)
    repository(root)
    (root / "modules/orders").mkdir(parents=True)
    (root / "modules/__init__.py").write_text("", encoding="utf-8")
    (root / "modules/orders/__init__.py").write_text("", encoding="utf-8")
    (root / "modules/orders/service.py").write_text("VALUE = 0\n", encoding="utf-8")
    (root / "modules/orders/other.py").write_text("OTHER = 0\n", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests/__init__.py").write_text("", encoding="utf-8")
    git(root, "add", "."); git(root, "commit", "-q", "-m", "existing behaviour")
    bootstrap_project(root, settings)
    state = create_change(root, "orders", product(), now=lambda: NOW)
    approve_change(root, state["change_id"], now=lambda: NOW)
    return temporary, root, state["change_id"]


def _review(project, context):
    return {"verdict": "pass", "summary": "ok", "findings": [], "questions": []}


class TestRootMatchingTests(unittest.TestCase):
    def test_patterns_cover_directories_globs_and_double_stars(self):
        roots = ["tests", "backend/**/src/test/**", "frontend/src/**/*.test.ts"]
        self.assertTrue(matches_test_root("tests/test_x.py", roots))
        self.assertTrue(matches_test_root("backend/ruoyi-modules/ruoyi-biz/src/test/java/A.java", roots))
        self.assertTrue(matches_test_root("frontend/src/utils/date.test.ts", roots))
        self.assertFalse(matches_test_root("backend/ruoyi-modules/ruoyi-biz/src/main/java/A.java", roots))
        self.assertFalse(matches_test_root("frontend/src/utils/date.ts", roots))
        self.assertFalse(matches_test_root("tests2/x.py", roots))


class CharacterizationGateTests(unittest.TestCase):
    def _services(self, change_id, implement, characterize, calls):
        def wrapped(project, context):
            calls.append(context)
            return characterize(project, context)
        return LoopServices(generate_smoke=lambda project, state: smoke_payload(change_id),
                            implement=implement, review=_review, characterize=wrapped)

    def _characterize(self, project, context):
        (project / "tests/test_characterize_service.py").write_text(GREEN_TEST, encoding="utf-8")
        return {"summary": "locked service", "touched": ["modules/orders/service.py"],
                "tests": ["tests/test_characterize_service.py"], "questions": []}

    def test_characterization_runs_green_before_the_first_attempt_and_is_committed(self):
        temporary, root, change_id = _repo({"unit_commands": [UNIT], "test_roots": ["tests"]})
        calls: list = []
        order: list = []

        def implement(project, context):
            order.append(("implement", context["characterization"]["touched"]))
            (project / "modules/orders/service.py").write_text("VALUE = 1\n", encoding="utf-8")
            return {"summary": "done", "questions": []}

        try:
            result = run_change(root, change_id, self._services(change_id, implement, self._characterize, calls))
            self.assertEqual(result["status"], "PASSED")
            self.assertEqual(len(calls), 1)
            self.assertEqual(order, [("implement", ["modules/orders/service.py"])])
            state = load_run_state(root, change_id)
            self.assertEqual(state["characterization"]["status"], "GREEN")
            subjects = git(root, "log", "--format=%s", "-6").splitlines()
            self.assertIn(f"test({change_id}): characterize touched paths", subjects)
            self.assertTrue((root / "tests/test_characterize_service.py").is_file())
        finally:
            temporary.cleanup()

    def test_modifying_an_undeclared_existing_file_is_reverted_characterized_and_retried(self):
        temporary, root, change_id = _repo({"unit_commands": [UNIT], "test_roots": ["tests"]})
        calls: list = []
        attempts: list = []

        def characterize(project, context):
            if context.get("extend"):
                (project / "tests/test_characterize_other.py").write_text(
                    GREEN_TEST.replace("service", "other").replace("VALUE", "OTHER"), encoding="utf-8")
                return {"summary": "locked other", "touched": context["extend"],
                        "tests": ["tests/test_characterize_other.py"], "questions": []}
            return self._characterize(project, context)

        def implement(project, context):
            attempts.append(context["attempt"])
            (project / "modules/orders/service.py").write_text("VALUE = 1\n", encoding="utf-8")
            if context["attempt"] == 1:
                (project / "modules/orders/other.py").write_text("OTHER = 1\n", encoding="utf-8")
            return {"summary": "done", "questions": []}

        try:
            result = run_change(root, change_id, self._services(change_id, implement, characterize, calls))
            self.assertEqual(result["status"], "PASSED")
            self.assertEqual(attempts, [1, 2])
            self.assertEqual(calls[1]["extend"], ["modules/orders/other.py"])
            state = load_run_state(root, change_id)
            self.assertEqual(state["failures"][0]["characterization"]["uncharacterized"], ["modules/orders/other.py"])
            self.assertEqual(sorted(state["characterization"]["touched"]),
                             ["modules/orders/other.py", "modules/orders/service.py"])
            self.assertEqual((root / "modules/orders/other.py").read_text(encoding="utf-8"), "OTHER = 0\n")
        finally:
            temporary.cleanup()

    def test_red_characterization_or_non_test_writes_stop_the_run(self):
        temporary, root, change_id = _repo({"unit_commands": [UNIT], "test_roots": ["tests"]})
        try:
            def red(project, context):
                (project / "tests/test_characterize_service.py").write_text(
                    GREEN_TEST.replace("assertIn", "assertNotIn"), encoding="utf-8")
                return {"summary": "wrong lock", "touched": ["modules/orders/service.py"],
                        "tests": ["tests/test_characterize_service.py"], "questions": []}
            result = run_change(root, change_id, self._services(change_id, lambda p, c: None, red, []))
            self.assertEqual(result["status"], "CHARACTERIZATION_FAILED")
        finally:
            temporary.cleanup()
        temporary, root, change_id = _repo({"unit_commands": [UNIT], "test_roots": ["tests"]})
        try:
            def leaks(project, context):
                (project / "modules/orders/service.py").write_text("VALUE = 2\n", encoding="utf-8")
                return {"summary": "edited code", "touched": [], "tests": [], "questions": []}
            result = run_change(root, change_id, self._services(change_id, lambda p, c: None, leaks, []))
            self.assertEqual(result["status"], "BOUNDARY_VIOLATION")
        finally:
            temporary.cleanup()

    def test_gate_is_skipped_when_the_project_declares_no_test_roots(self):
        temporary, root, change_id = _repo({"unit_commands": [UNIT]})
        calls: list = []

        def implement(project, context):
            (project / "modules/orders/service.py").write_text("VALUE = 1\n", encoding="utf-8")
            return {"summary": "done", "questions": []}

        try:
            result = run_change(root, change_id, self._services(change_id, implement, self._characterize, calls))
            self.assertEqual(result["status"], "PASSED")
            self.assertEqual(calls, [])
            self.assertEqual(load_run_state(root, change_id)["characterization"]["status"], "SKIPPED")
        finally:
            temporary.cleanup()


if __name__ == "__main__":
    unittest.main()
