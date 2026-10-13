"""Acceptance tests are fast or heavy, and one test never mixes must-be-red with must-be-green rows."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from storyloop.approvals import approve_change
from storyloop.baseline import bootstrap_project
from storyloop.changes import create_change
from storyloop.models import StoryLoopError
from storyloop.smoke import install_smoke_suite
from storyloop.acceptance_repair import repair_smoke
from storyloop.project_loop import LoopServices, load_run_state, run_change
from storyloop.smoke_runner import run_smoke
from tests.support import product
from tests.test_baseline_behavior import repository

NOW = datetime(2026, 10, 13, tzinfo=timezone.utc)


def two_row_product() -> dict:
    """AC-1 asserts new behaviour (red first); AC-2 asserts that existing behaviour keeps holding."""
    value = product()
    value["stories"][0]["acceptance"].append({
        "id": "AC-2", "given": "an existing room", "when": "listed", "then": "it is still listed",
        "kind": "automated", "row_type": "regression"})
    return value


def approved(root: Path, value: dict) -> str:
    repository(root)
    bootstrap_project(root, {"test_commands": []})
    change_id = create_change(root, "rooms", value, now=lambda: NOW)["change_id"]
    approve_change(root, change_id, now=lambda: NOW)
    return change_id


def test_entry(test_id: str, criteria: list[str], tier: str | None = None) -> dict:
    entry = {"id": test_id, "criteria": criteria, "command": ["{python}", "-V"], "timeout_seconds": 10}
    if tier:
        entry["tier"] = tier
    return entry


class ManifestTierTests(unittest.TestCase):
    def test_a_test_cannot_mix_expected_states(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            change_id = approved(root, two_row_product())
            files = [{"path": "t.py", "content": "pass\n"}]
            with self.assertRaisesRegex(StoryLoopError, "expected"):
                install_smoke_suite(root, change_id, files, {"version": 1, "tests": [
                    test_entry("T1", ["AC-1", "AC-2"])]})
            install_smoke_suite(root, change_id, files, {"version": 1, "tests": [
                test_entry("T1", ["AC-1"]), test_entry("T2", ["AC-2"])]})

    def test_a_heavy_criterion_needs_a_heavy_test(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            value = product()
            value["stories"][0]["acceptance"][0]["oracle_class"] = "auto-heavy"
            change_id = approved(root, value)
            files = [{"path": "t.py", "content": "pass\n"}]
            with self.assertRaisesRegex(StoryLoopError, "tier"):
                install_smoke_suite(root, change_id, files, {"version": 1, "tests": [test_entry("T1", ["AC-1"])]})
            install_smoke_suite(root, change_id, files, {"version": 1, "tests": [
                test_entry("T1", ["AC-1"], "heavy")]})



def script(exit_code: int, output: str = "") -> str:
    return f"import sys; print({output!r}); sys.exit({exit_code})\n"


def suite(change_id: str, red_exit: int, green_exit: int, red_output: str = "") -> dict:
    return {"files": [{"path": "red.py", "content": script(red_exit, red_output)},
                      {"path": "keep.py", "content": script(green_exit)}],
            "manifest": {"version": 1, "tests": [
                {"id": "NEW", "criteria": ["AC-1"], "command": ["{python}", f"acceptance/changes/{change_id}/red.py"],
                 "timeout_seconds": 10},
                {"id": "KEEP", "criteria": ["AC-2"], "command": ["{python}", f"acceptance/changes/{change_id}/keep.py"],
                 "timeout_seconds": 10}]}}


class RedByRowTypeTests(unittest.TestCase):
    def _prove(self, red_exit: int, green_exit: int, red_output: str = ""):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        change_id = approved(root, two_row_product())
        payload = suite(change_id, red_exit, green_exit, red_output)
        install_smoke_suite(root, change_id, payload["files"], payload["manifest"])
        return root, change_id

    def test_new_rows_red_and_kept_rows_green_prove_red(self):
        root, change_id = self._prove(1, 0)
        self.assertEqual(run_smoke(root, change_id, expect_red=True)["status"], "RED")

    def test_a_kept_row_failing_on_old_code_is_an_oracle_defect(self):
        root, change_id = self._prove(1, 1)
        with self.assertRaises(StoryLoopError) as raised:
            run_smoke(root, change_id, expect_red=True)
        self.assertEqual(raised.exception.code, "ORACLE_DEFECT")
        self.assertEqual(raised.exception.details["criteria_failing"], ["AC-2"])

    def test_a_new_row_passing_on_old_code_is_existing_green(self):
        root, change_id = self._prove(0, 0)
        with self.assertRaises(StoryLoopError) as raised:
            run_smoke(root, change_id, expect_red=True)
        self.assertEqual(raised.exception.code, "EXISTING_GREEN")

    def test_a_failure_with_zero_executed_tests_is_not_red(self):
        root, change_id = self._prove(1, 0, "Ran 0 tests in 0.000s")
        with self.assertRaisesRegex(StoryLoopError, "environment error"):
            run_smoke(root, change_id, expect_red=True)


class OracleDefectLoopTests(unittest.TestCase):
    def test_the_run_stops_at_oracle_defect_and_acceptance_repair_resumes_it(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            change_id = approved(root, two_row_product())

            def implement(project, context):
                (project / "modules/orders").mkdir(parents=True, exist_ok=True)
                (project / "modules/orders/service.py").write_text("VALUE = 1\n", encoding="utf-8")
                return {"summary": "done", "questions": []}

            review = lambda project, context: {"verdict": "pass", "summary": "ok", "findings": [], "questions": []}
            broken = LoopServices(generate_smoke=lambda p, s: suite(change_id, 1, 1), implement=implement, review=review)
            stopped = run_change(root, change_id, broken)
            self.assertEqual(stopped["status"], "ORACLE_DEFECT")
            self.assertIn("acceptance-repair", stopped["next"])
            self.assertEqual(load_run_state(root, change_id)["status"], "ORACLE_DEFECT")

            fixed_check = ("from pathlib import Path\nimport sys\n"
                           "sys.exit(0 if Path('modules/orders/service.py').is_file() else 1)\n")
            repaired = suite(change_id, 1, 0)
            repaired["files"][0]["content"] = fixed_check
            seen = {}

            def regenerate(project, change):
                seen["evidence"] = change["repair"]["failure_evidence"]
                return repaired

            result = repair_smoke(root, change_id, "KEEP 断言写错了",
                                  LoopServices(generate_smoke=regenerate, implement=implement, review=review))
            self.assertEqual(result["status"], "ACCEPTANCE_REPAIRED")
            self.assertEqual(seen["evidence"]["status"], "ORACLE_DEFECT")
            done = run_change(root, change_id, LoopServices(generate_smoke=lambda p, s: repaired,
                                                            implement=implement, review=review))
            self.assertEqual(done["status"], "PASSED", done)


if __name__ == "__main__":
    unittest.main()
