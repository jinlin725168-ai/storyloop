import importlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tests.lesson_support import cleanup_repo
from tests.lesson_support import git_repo, sample


def storyloop(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "storyloop", *args],
                          capture_output=True, text=True, encoding="utf-8", check=False)


class CliTests(unittest.TestCase):
    def test_lessons_json_exposes_vector_signal(self):
        repo = git_repo(lessons=[sample()])
        self.addCleanup(cleanup_repo, repo)
        baseline = repo / ".storyloop/baseline.json"
        baseline.parent.mkdir(parents=True, exist_ok=True)
        baseline.write_text("{}\n", encoding="utf-8")

        result = storyloop("lessons", str(repo), "--json")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("vector_signal", json.loads(result.stdout))

    def test_lessons_text_renders_chinese_status_and_upgrade_advice(self):
        entry = sample(status="needs-review")
        repo = git_repo(lessons=[entry])
        self.addCleanup(cleanup_repo, repo)
        baseline = repo / ".storyloop/baseline.json"
        baseline.parent.mkdir(parents=True, exist_ok=True)
        baseline.write_text("{}\n", encoding="utf-8")
        state = repo / ".storyloop/state/lessons.json"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps({"L-0001": {"recurrences": ["A", "B"]}},
                                    ensure_ascii=False), encoding="utf-8")

        result = storyloop("lessons", str(repo))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("需复查", result.stdout)
        self.assertIn("建议升级为检查", result.stdout)

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
