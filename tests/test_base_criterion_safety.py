"""A base target proof opens only its exact criterion paths."""

from pathlib import Path
import tempfile
import unittest

from storyloop.base_upgrade import propose_base_change
from storyloop.baseline import bootstrap_project
from storyloop.changes import load_change
from storyloop.models import StoryLoopError
from storyloop.project_boundaries import audit_base_scope, role_path_boundary
from storyloop.storage import write_json
from tests.test_baseline_behavior import repository


class BaseCriterionSafetyTests(unittest.TestCase):
    def test_agent_can_write_approved_smoke_path_but_not_neighbor(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            bootstrap_project(root, {"test_commands": [], "base_build_commands": []})
            proof_file = root / ".storyloop/proof-input.json"
            write_json(proof_file, {
                "criterion_paths": ["acceptance/smoke/check_coordinates.py"],
                "check_ref": {"kind": "system_smoke", "id": "coordinates"},
                "violation_path": "pom.xml"})
            proposal = propose_base_change(root, ["pom.xml", "acceptance/smoke/check_coordinates.py"],
                                           "新增坐标检查", proof_file=proof_file)
            change = load_change(root, proposal["change_id"])
            boundary = role_path_boundary(root, "implement", {"change": change})
            self.assertIn("acceptance/smoke/check_coordinates.py", boundary["writable_paths"])
            self.assertNotIn("acceptance/smoke/**", boundary["forbidden_paths"])
            audit_base_scope(root, change, set(change["product"]["base"]["paths"]))
            with self.assertRaises(StoryLoopError):
                audit_base_scope(root, change, {"acceptance/smoke/other.py"})


if __name__ == "__main__":
    unittest.main()
