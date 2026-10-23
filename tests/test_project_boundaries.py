from pathlib import Path
from dataclasses import replace
from types import MappingProxyType
import tempfile
import unittest
from unittest.mock import patch

from storyloop import change_types
from storyloop.change_types import CHANGE_TYPES
from storyloop.baseline import bootstrap_project
from storyloop.project_boundaries import audit_project_changes, role_path_boundary
from tests.test_baseline_behavior import git, repository


class ProjectBoundaryTests(unittest.TestCase):
    def test_type_writable_scope_controls_module_and_role_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            bootstrap_project(root)
            target = root / "modules/orders/new.py"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("VALUE = 1\n", encoding="utf-8")
            normal = role_path_boundary(root, "implement", {"change": {"product": {"nature": "feature"}}})
            self.assertTrue(any(path.startswith("modules/") for path in normal["writable_paths"]))
            policy = replace(CHANGE_TYPES["feature"], writable_scope=("test_roots",))
            with patch.object(change_types, "CHANGE_TYPES",
                              MappingProxyType({**CHANGE_TYPES, "feature": policy})):
                result = audit_project_changes(root, "FEAT-20261023-001")
                role = role_path_boundary(root, "implement", {"change": {"product": {"nature": "feature"}}})
            self.assertEqual([(item["path"], item["category"]) for item in result["violations"]],
                             [("modules/orders/new.py", "OUT_OF_SCOPE")])
            self.assertFalse(any(path.startswith("modules/") for path in role["writable_paths"]))
            demo = replace(CHANGE_TYPES["feature"], id_prefix="DEMO", writable_scope=("test_roots",))
            with patch.object(change_types, "CHANGE_TYPES", MappingProxyType({**CHANGE_TYPES, "demo": demo})):
                result = audit_project_changes(root, "DEMO-20261023-001")
            self.assertEqual([item["category"] for item in result["violations"]], ["OUT_OF_SCOPE"])

    def test_agent_write_to_lessons_is_a_violation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            bootstrap_project(root)
            lesson = root / "openspec/lessons/L-0001.md"
            lesson.parent.mkdir(parents=True)
            lesson.write_text("agent output\n", encoding="utf-8")

            result = audit_project_changes(root, "FEAT-20260916-001")

            self.assertEqual(result["violations"][0]["category"], "LESSON_PROTECTED")

    def test_lessons_are_always_in_role_forbidden_paths(self):
        from storyloop.project_boundaries import role_path_boundary

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            bootstrap_project(root)
            boundary = role_path_boundary(root, "implement", {})
            self.assertIn("openspec/lessons/**", boundary["forbidden_paths"])

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
            smoke = root / "acceptance/changes/FEAT-20260916-001/test.py"
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
