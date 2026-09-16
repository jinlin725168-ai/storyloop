import json
from pathlib import Path
import tempfile
import unittest

from storyloop.models import StoryLoopError
from storyloop.project_profile import detect_project, validate_project_settings


class ProjectProfileBehaviorTests(unittest.TestCase):
    def test_detects_mixed_project_without_claiming_ci_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "pom.xml").write_text("<project/>", encoding="utf-8")
            (root / "pyproject.toml").write_text("[project]\nname='sample'\n", encoding="utf-8")
            (root / ".github/workflows").mkdir(parents=True)
            workflow = root / ".github/workflows/ci.yml"
            workflow.write_text("name: existing\n", encoding="utf-8")

            profile = detect_project(root)

            self.assertEqual(profile["version"], 1)
            self.assertEqual(profile["kinds"], ["maven", "python"])
            self.assertIn(["{python}", "-m", "unittest", "discover"], profile["test_commands"])
            self.assertTrue(["mvn", "test"] in profile["test_commands"]
                            or any("mvn" in item for item in profile["warnings"]))
            self.assertIn("pom.xml", profile["integration_paths"])
            self.assertIn(".github/workflows/ci.yml", profile["protected_paths"])
            self.assertNotIn(".github/workflows/ci.yml", profile["generated_paths"])

    def test_prefers_package_manager_lock_and_existing_test_script(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "package.json").write_text(
                json.dumps({"scripts": {"test": "node --test"}}), encoding="utf-8"
            )
            (root / "pnpm-lock.yaml").write_text("lockfileVersion: '9'\n", encoding="utf-8")

            profile = detect_project(root)

            self.assertIn("node", profile["kinds"])
            self.assertTrue(["pnpm", "test"] in profile["test_commands"]
                            or any("pnpm" in item for item in profile["warnings"]))

    def test_rejects_shell_string_command_override(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(StoryLoopError, "argv"):
                validate_project_settings(
                    Path(folder), {"test_commands": ["python -m unittest"]}
                )


if __name__ == "__main__":
    unittest.main()
