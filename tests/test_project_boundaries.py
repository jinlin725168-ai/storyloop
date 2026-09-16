from pathlib import Path
import tempfile
import unittest

from storyloop.baseline import bootstrap_project
from storyloop.project_boundaries import audit_project_changes
from tests.test_baseline_behavior import git, repository


class ProjectBoundaryTests(unittest.TestCase):
    def test_allows_new_modules_but_routes_integration_and_base_to_base_change(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            (root / "core.py").write_text("STABLE = True\n", encoding="utf-8")
            git(root, "add", "core.py")
            git(root, "commit", "-q", "-m", "core")
            bootstrap_project(root)
            (root / "modules/orders").mkdir(parents=True)
            (root / "modules/orders/service.py").write_text("VALUE = 1\n", encoding="utf-8")
            (root / "pom.xml").write_text("<project><modules/></project>\n", encoding="utf-8")
            (root / "core.py").write_text("STABLE = False\n", encoding="utf-8")

            result = audit_project_changes(root, "FEAT-20260916-001")

            allowed = {item["path"]: item["category"] for item in result["allowed"]}
            violations = {item["path"]: item["category"] for item in result["violations"]}
            self.assertEqual(allowed["modules/orders/service.py"], "FEATURE")
            self.assertEqual(violations["pom.xml"], "INTEGRATION_CHANGE")
            self.assertEqual(violations["core.py"], "FOUNDATION_CHANGE")
            self.assertEqual(result["status"], "BASE_CHANGE_REQUIRED")
            self.assertTrue(Path(result["base_proposal"]).is_file())

    def test_ci_and_committed_acceptance_contracts_are_never_implementation_outputs(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            bootstrap_project(root)
            contract = root / "openspec/changes/FEAT-20260916-001/proposal.md"
            smoke = root / "acceptance/smoke/FEAT-20260916-001/test.py"
            contract.parent.mkdir(parents=True)
            smoke.parent.mkdir(parents=True)
            contract.write_text("approved\n", encoding="utf-8")
            smoke.write_text("approved\n", encoding="utf-8")
            git(root, "add", "openspec", "acceptance")
            git(root, "commit", "-q", "-m", "contract")
            contract.write_text("weakened\n", encoding="utf-8")
            smoke.write_text("weakened\n", encoding="utf-8")
            (root / ".github/workflows/ci.yml").write_text("changed\n", encoding="utf-8")

            result = audit_project_changes(root, "FEAT-20260916-001")

            violations = {item["path"]: item["category"] for item in result["violations"]}
            self.assertEqual(violations[contract.relative_to(root).as_posix()], "CONTRACT_PROTECTED")
            self.assertEqual(violations[smoke.relative_to(root).as_posix()], "CONTRACT_PROTECTED")
            self.assertEqual(violations[".github/workflows/ci.yml"], "CI_PROTECTED")


if __name__ == "__main__":
    unittest.main()
