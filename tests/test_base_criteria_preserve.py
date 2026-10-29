"""Human base candidates may extend criteria but must retain existing cases."""

from pathlib import Path
import json
import sys
import tempfile
import unittest

from storyloop.approvals import approve_change
from storyloop.base_execution import record_base_review
from storyloop.base_upgrade import propose_base_change
from storyloop.manual_acceptance import record_manual_acceptance
from storyloop.onboarding_cli import bootstrap_with_project_checks
from storyloop.project_loop import run_change
from storyloop.system_smoke import load_system_smoke
from tests.test_baseline_behavior import git, repository


class BaseCriteriaPreservationTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        repository(self.root)
        smoke = self.root / "acceptance/smoke"
        smoke.mkdir(parents=True)
        (smoke / "old.py").write_text("print('old green')\n", encoding="utf-8")
        self._write_manifest([self._case("old", "old.py")])
        tests = self.root / "tests"
        tests.mkdir()
        (tests / "test_existing.py").write_text("def test_old(): pass\n", encoding="utf-8")
        git(self.root, "add", ".")
        git(self.root, "commit", "-q", "-m", "initial criteria")
        bootstrap_with_project_checks(self.root, {
            "unit_commands": [[sys.executable, "-c", "print('unit green')"]],
            "integration_commands": [], "check_commands": [],
            "base_build_commands": [], "test_roots": ["tests"],
        })

    def _case(self, case_id, filename):
        return {"id": case_id, "description": case_id,
                "command": ["{python}", f"acceptance/smoke/{filename}"],
                "timeout_seconds": 30}

    def _write_manifest(self, cases):
        (self.root / "acceptance/smoke/manifest.json").write_text(
            json.dumps({"version": 1, "tests": cases}), encoding="utf-8")

    def _candidate(self, paths, edit):
        proposal = propose_base_change(self.root, paths, "更新判据")
        change_id = proposal["change_id"]
        approve_change(self.root, change_id)
        self.assertEqual(run_change(self.root, change_id)["status"],
                         "HUMAN_IMPLEMENTATION_REQUIRED")
        edit()
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "human candidate")
        return run_change(self.root, change_id)

    def test_removed_smoke_case_is_rejected_with_guidance(self):
        result = self._candidate(["acceptance/smoke/manifest.json"],
                                 lambda: self._write_manifest([self._case("new", "old.py")]))
        self.assertEqual(result["status"], "HUMAN_IMPLEMENTATION_REQUIRED")
        self.assertIn("old", result["reason"])
        self.assertIn("恢复", result["reason"])

    def test_removed_original_test_file_is_rejected_with_guidance(self):
        result = self._candidate(["tests/test_existing.py"],
                                 lambda: (self.root / "tests/test_existing.py").unlink())
        self.assertEqual(result["status"], "HUMAN_IMPLEMENTATION_REQUIRED")
        self.assertIn("tests/test_existing.py", result["reason"])
        self.assertIn("恢复", result["reason"])

    def test_removed_original_smoke_check_is_rejected(self):
        def edit():
            (self.root / "acceptance/smoke/old.py").unlink()
            (self.root / "acceptance/smoke/new.py").write_text("print('new green')\n", encoding="utf-8")
            self._write_manifest([self._case("old", "new.py")])

        result = self._candidate(["acceptance/smoke/manifest.json", "acceptance/smoke/old.py",
                                  "acceptance/smoke/new.py"], edit)
        self.assertEqual(result["status"], "HUMAN_IMPLEMENTATION_REQUIRED")
        self.assertIn("acceptance/smoke/old.py", result["reason"])

    def test_added_smoke_case_and_modified_test_are_accepted(self):
        def edit():
            (self.root / "acceptance/smoke/new.py").write_text("print('new green')\n", encoding="utf-8")
            self._write_manifest([self._case("old", "old.py"), self._case("new", "new.py")])
            (self.root / "tests/test_existing.py").write_text("def test_old(): assert True\n", encoding="utf-8")

        result = self._candidate(["acceptance/smoke/manifest.json", "acceptance/smoke/new.py",
                                  "tests/test_existing.py"], edit)
        self.assertEqual(result["status"], "BASE_REVIEW_REQUIRED")

    def test_merged_smoke_suite_becomes_protected_suite(self):
        def edit():
            (self.root / "acceptance/smoke/new.py").write_text("print('new green')\n", encoding="utf-8")
            self._write_manifest([self._case("old", "old.py"), self._case("new", "new.py")])

        proposal = propose_base_change(self.root, ["acceptance/smoke/manifest.json",
                                                   "acceptance/smoke/new.py"], "扩充系统冒烟")
        change_id = proposal["change_id"]
        approve_change(self.root, change_id)
        self.assertEqual(run_change(self.root, change_id)["status"], "HUMAN_IMPLEMENTATION_REQUIRED")
        edit()
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "human candidate")
        self.assertEqual(run_change(self.root, change_id)["status"], "BASE_REVIEW_REQUIRED")
        git(self.root, "config", "user.name", "Independent Reviewer")
        record_base_review(self.root, change_id, "pass", "已核对新增冒烟")
        self.assertEqual(run_change(self.root, change_id)["status"], "MANUAL_ACCEPTANCE_REQUIRED")
        record_manual_acceptance(self.root, change_id, ["BASE-ACCEPT"], "新增检查已通过")
        self.assertEqual(run_change(self.root, change_id)["status"], "FINALIZING")
        self.assertEqual(run_change(self.root, change_id)["status"], "PASSED")
        self.assertEqual({row["id"] for row in load_system_smoke(self.root)["tests"]},
                         {"old", "new"})

    def test_non_criteria_base_candidate_is_unchanged(self):
        result = self._candidate(["pom.xml"],
                                 lambda: (self.root / "pom.xml").write_text("<project>new</project>\n"))
        self.assertEqual(result["status"], "BASE_REVIEW_REQUIRED")


if __name__ == "__main__":
    unittest.main()
