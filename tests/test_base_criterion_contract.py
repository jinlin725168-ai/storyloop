"""Human-approved target proof is part of new base contracts only."""

from pathlib import Path
import tempfile
import unittest

from storyloop.approvals import approve_change
from storyloop.base_upgrade import propose_base_change
from storyloop.baseline import bootstrap_project
from storyloop.changes import contract_digest, load_change
from storyloop.models import StoryLoopError
from storyloop.project_boundaries import audit_base_scope
from storyloop.storage import write_json
from tests.test_baseline_behavior import repository


class BaseCriterionContractTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        repository(self.root)
        bootstrap_project(self.root, {"test_commands": [], "base_build_commands": []})
        self.proof_path = self.root / ".storyloop/proof-input.json"

    def proof(self, **changes):
        value = {"criterion_paths": ["acceptance/smoke/check_coordinates.py"],
                 "check_ref": {"kind": "system_smoke", "id": "coordinates"},
                 "violation_path": "pom.xml"}
        value.update(changes)
        write_json(self.proof_path, value)
        return self.proof_path

    def test_old_base_contract_keeps_its_digest_and_shape(self):
        proposal = propose_base_change(self.root, ["pom.xml"], "更新构建配置")
        change = load_change(self.root, proposal["change_id"])
        self.assertNotIn("target_proof", change["product"]["base"])
        self.assertEqual(approve_change(self.root, proposal["change_id"])["approval"]
                         ["contract_digest"], contract_digest(change["product"]))

    def test_proof_is_frozen_in_new_proposal_and_approved_contract(self):
        paths = ["pom.xml", "acceptance/smoke/manifest.json",
                 "acceptance/smoke/check_coordinates.py"]
        proposal = propose_base_change(
            self.root, paths, "更新坐标",
            proof_file=self.proof(criterion_paths=["acceptance/smoke/manifest.json",
                                                   "acceptance/smoke/check_coordinates.py"]))
        change = load_change(self.root, proposal["change_id"])
        self.assertEqual(change["product"]["base"]["target_proof"]["violation_path"],
                         "pom.xml")
        self.assertEqual(approve_change(self.root, proposal["change_id"])["approval"]
                         ["contract_digest"], contract_digest(change["product"]))
        audit_base_scope(self.root, change, set(paths))

    def test_criterion_path_requires_proof_and_existing_regular_violation(self):
        with self.assertRaisesRegex(StoryLoopError, "proof|判据"):
            propose_base_change(self.root, ["pom.xml", "acceptance/smoke/check_coordinates.py"],
                                "新增判据")
        with self.assertRaisesRegex(StoryLoopError, "violation|目标"):
            propose_base_change(self.root, ["pom.xml", "acceptance/smoke/check_coordinates.py"],
                                "新增判据", proof_file=self.proof(violation_path="new.xml"))

    def test_proof_rejects_unapproved_criterion_and_wrong_scope(self):
        with self.assertRaisesRegex(StoryLoopError, "criterion|判据"):
            propose_base_change(self.root, ["pom.xml"], "更新坐标", proof_file=self.proof())
        with self.assertRaisesRegex(StoryLoopError, "scope|foundation"):
            propose_base_change(self.root, ["pom.xml", "acceptance/smoke/check_coordinates.py"],
                                "更新坐标", proof_file=self.proof(), scope="skills")


if __name__ == "__main__":
    unittest.main()
