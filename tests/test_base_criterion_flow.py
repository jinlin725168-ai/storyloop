"""A candidate must prove a changed criterion red on the old target and green on itself."""

from pathlib import Path
import json
import sys
import tempfile
import unittest

from storyloop.approvals import approve_change
from storyloop.base_upgrade import propose_base_change
from storyloop.onboarding_cli import bootstrap_with_project_checks
from storyloop.project_loop import run_change
from storyloop.storage import load_json, write_json
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


if __name__ == "__main__":
    unittest.main()
