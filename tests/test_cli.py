import importlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


def storyloop(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "storyloop", *args],
                          capture_output=True, text=True, encoding="utf-8", check=False)


class CliTests(unittest.TestCase):
    def test_help_lists_the_product_commands_only(self):
        self.assertIsNotNone(importlib.util.find_spec("storyloop.cli"), "CLI must exist")
        result = storyloop("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        for command in ["init", "change", "approve", "run", "status", "log", "accept", "report", "ci"]:
            self.assertIn(command, result.stdout)
        for removed in ["discuss", "prepare", "demo", "stage"]:
            self.assertNotIn(removed, result.stdout.split("positional arguments")[0])

    def test_init_needs_a_git_repository_and_says_so(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder) / "project"
            project.mkdir()
            result = storyloop("init", str(project))
            self.assertEqual(result.returncode, 2)
            self.assertIn("Git repository", json.loads(result.stderr)["message"])
            self.assertFalse((project / ".storyloop").exists())

    def test_commands_on_an_uninitialized_project_point_to_init(self):
        with tempfile.TemporaryDirectory() as folder:
            result = storyloop("status", folder, "--change", "FEAT-1")
            self.assertEqual(result.returncode, 2)
            self.assertIn("storyloop init", json.loads(result.stderr)["message"])


if __name__ == "__main__":
    unittest.main()
