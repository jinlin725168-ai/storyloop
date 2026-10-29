"""A candidate must prove a changed criterion red on the old target and green on itself."""

from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest import mock

from storyloop.approvals import approve_change
from storyloop import base_execution
from storyloop.base_execution import record_base_review
from storyloop.base_upgrade import propose_base_change
from storyloop.baseline import load_baseline
from storyloop.manual_acceptance import record_manual_acceptance
from storyloop.onboarding_cli import bootstrap_with_project_checks
from storyloop.project_loop import run_change
from storyloop.storage import load_json, write_json
from storyloop.system_smoke import load_system_smoke
from tests.test_baseline_behavior import git, repository


class BaseCriterionFlowTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        repository(self.root)
        (self.root / "pom.xml").write_text("org.old:app:1.0\n", encoding="utf-8")
        smoke = self.root / "acceptance/smoke"
        smoke.mkdir(parents=True)
        (smoke / "old.py").write_text("print('green')\n", encoding="utf-8")
        write_json(smoke / "manifest.json", {"version": 1, "tests": [
            {"id": "old", "description": "old", "command": ["{python}",
             "acceptance/smoke/old.py"], "timeout_seconds": 30}]})
        git(self.root, "add", ".")
        git(self.root, "commit", "-q", "-m", "old smoke")
        bootstrap_with_project_checks(self.root, {
            "unit_commands": [[sys.executable, "-c", "print('green')"]],
            "integration_commands": [], "check_commands": [], "base_build_commands": []})
        self.paths = ["pom.xml", "acceptance/smoke/manifest.json",
                      "acceptance/smoke/check_coordinates.py"]
        proof = self.root / ".storyloop/proof-input.json"
        write_json(proof, {"criterion_paths": self.paths[1:],
                           "check_ref": {"kind": "system_smoke", "id": "coordinates"},
                           "violation_path": "pom.xml"})
        proposal = propose_base_change(self.root, self.paths, "更新坐标", proof_file=proof)
        self.change_id = proposal["change_id"]
        approve_change(self.root, self.change_id)

    def _candidate(self):
        result = run_change(self.root, self.change_id, implementer="human")
        self.assertEqual(result["status"], "HUMAN_IMPLEMENTATION_REQUIRED")
        (self.root / "pom.xml").write_text("com.example:app:1.1\n", encoding="utf-8")
        (self.root / "acceptance/smoke/check_coordinates.py").write_text(
            "from pathlib import Path\n"
            "assert Path('pom.xml').read_text().strip() == 'com.example:app:1.1'\n",
            encoding="utf-8")
        manifest = self.root / "acceptance/smoke/manifest.json"
        value = json.loads(manifest.read_text(encoding="utf-8"))
        value["tests"].append({"id": "coordinates", "description": "coordinates",
                               "command": ["{python}", "acceptance/smoke/check_coordinates.py"],
                               "timeout_seconds": 30})
        write_json(manifest, value)
        git(self.root, "add", "pom.xml", "acceptance/smoke")
        git(self.root, "commit", "-q", "-m", "coordinates")

    def test_candidate_old_blob_is_red_and_new_tree_is_green(self):
        self._candidate()
        result = run_change(self.root, self.change_id)
        self.assertEqual(result["status"], "BASE_REVIEW_REQUIRED")
        evidence = load_json(self.root / ".storyloop/evidence" / self.change_id /
                             "base-candidate-green.json")
        proof = evidence["target_proof"]
        self.assertEqual(proof["violation"]["status"], "FAILED")
        self.assertEqual(proof["candidate"]["status"], "PASSED")
        self.assertEqual(len(proof["source_blob_oid"]), 40)
        self.assertEqual(len(proof["candidate_tree_oid"]), 40)

    def test_false_red_does_not_advance_to_review(self):
        self._candidate()
        (self.root / "acceptance/smoke/check_coordinates.py").write_text(
            "print('always green')\n", encoding="utf-8")
        git(self.root, "add", "acceptance/smoke/check_coordinates.py")
        git(self.root, "commit", "-q", "-m", "false red")
        result = run_change(self.root, self.change_id)
        self.assertNotEqual(result["status"], "BASE_REVIEW_REQUIRED")

    def test_merged_smoke_adoption_is_idempotent(self):
        old_version = load_baseline(self.root)["baseline_version"]
        self._candidate()
        result = run_change(self.root, self.change_id)
        self.assertEqual(result["status"], "BASE_REVIEW_REQUIRED", result)
        git(self.root, "config", "user.name", "Independent Reviewer")
        record_base_review(self.root, self.change_id, "pass", "核对坐标判据与实现")
        self.assertEqual(run_change(self.root, self.change_id)["status"],
                         "MANUAL_ACCEPTANCE_REQUIRED")
        record_manual_acceptance(self.root, self.change_id, ["BASE-ACCEPT"], "坐标已更新")
        self.assertEqual(run_change(self.root, self.change_id)["status"], "FINALIZING")
        self.assertEqual(run_change(self.root, self.change_id)["status"], "PASSED")
        self.assertEqual(run_change(self.root, self.change_id)["status"], "PASSED")
        self.assertEqual(load_system_smoke(self.root)["tests"][-1]["id"], "coordinates")
        self.assertNotEqual(load_baseline(self.root)["baseline_version"], old_version)

    def test_adoption_interruption_resumes_without_second_version(self):
        self._candidate()
        result = run_change(self.root, self.change_id)
        self.assertEqual(result["status"], "BASE_REVIEW_REQUIRED", result)
        git(self.root, "config", "user.name", "Independent Reviewer")
        record_base_review(self.root, self.change_id, "pass", "核对坐标判据与实现")
        run_change(self.root, self.change_id)
        record_manual_acceptance(self.root, self.change_id, ["BASE-ACCEPT"], "已核对")
        self.assertEqual(run_change(self.root, self.change_id)["status"], "FINALIZING")
        with mock.patch("storyloop.system_smoke.adopt_system_smoke",
                        side_effect=RuntimeError("interrupted adoption")):
            with self.assertRaisesRegex(RuntimeError, "interrupted adoption"):
                run_change(self.root, self.change_id)
        self.assertEqual(run_change(self.root, self.change_id)["status"], "PASSED")
        self.assertEqual(load_baseline(self.root)["baseline_version"], "1.0.1")
        self.assertEqual(load_system_smoke(self.root)["tests"][-1]["id"], "coordinates")

    def test_probe_write_before_state_restarts(self):
        self._candidate()
        original = base_execution.transition

        def interrupt(project, state, target, **kwargs):
            if kwargs.get("event") == "human_candidate_ready":
                raise RuntimeError("interrupted after proof write")
            return original(project, state, target, **kwargs)

        with mock.patch.object(base_execution, "transition", side_effect=interrupt):
            with self.assertRaisesRegex(RuntimeError, "interrupted after proof write"):
                run_change(self.root, self.change_id)
        evidence = self.root / ".storyloop/evidence" / self.change_id / "base-candidate-green.json"
        self.assertTrue(evidence.exists())
        self.assertEqual(run_change(self.root, self.change_id)["status"],
                         "HUMAN_IMPLEMENTATION_REQUIRED")
        self.assertFalse(evidence.exists())
        result = run_change(self.root, self.change_id)
        self.assertEqual(result["status"], "BASE_REVIEW_REQUIRED", result)

    def test_probe_tamper_or_candidate_change_restarts(self):
        self._candidate()
        self.assertEqual(run_change(self.root, self.change_id)["status"], "BASE_REVIEW_REQUIRED")
        path = self.root / ".storyloop/evidence" / self.change_id / "base-candidate-green.json"
        evidence = load_json(path)
        evidence["target_proof"]["candidate"]["status"] = "FAILED"
        write_json(path, evidence)
        self.assertEqual(run_change(self.root, self.change_id)["status"],
                         "HUMAN_IMPLEMENTATION_REQUIRED")
        result = run_change(self.root, self.change_id)
        self.assertEqual(result["status"], "BASE_REVIEW_REQUIRED", result)


if __name__ == "__main__":
    unittest.main()
