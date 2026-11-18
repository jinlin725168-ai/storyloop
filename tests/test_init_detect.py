"""One-command onboarding (roadmap F2): `init --detect` proposes, `--yes` locks, readiness runs."""
import json
from pathlib import Path
import tempfile
import unittest

from storyloop.cli import dispatch, parser
from storyloop.init_detect import agent_config, propose_settings
from storyloop.models import StoryLoopError
from tests.test_baseline_behavior import git


def _python_repo(root: Path, *, pytest: bool = False) -> None:
    git(root, "init", "-q")
    git(root, "config", "user.email", "storyloop@example.invalid")
    git(root, "config", "user.name", "StoryLoop Test")
    (root / "app").mkdir()
    (root / "app/__init__.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests/__init__.py").write_text("", encoding="utf-8")
    (root / "tests/test_app.py").write_text(
        "import unittest\nfrom app import add\n\n\nclass T(unittest.TestCase):\n"
        "    def test_add(self):\n        self.assertEqual(add(1, 2), 3)\n", encoding="utf-8")
    (root / "pyproject.toml").write_text("[project]\nname = 'app'\nversion = '0'\n", encoding="utf-8")
    (root / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    if pytest:
        (root / "tests/conftest.py").write_text("", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "initial")


class InitDetectTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def test_proposes_source_and_test_roots_and_the_unittest_command(self):
        _python_repo(self.root)
        settings = propose_settings(self.root)["settings"]
        self.assertEqual(settings["module_roots"], ["app"])
        self.assertEqual(settings["test_roots"], ["tests"])
        self.assertEqual(settings["unit_commands"], [["{python}", "-m", "unittest", "discover"]])
        self.assertIn("__pycache__", settings["scratch_paths"])

    def test_a_pytest_project_gets_a_pytest_command(self):
        _python_repo(self.root, pytest=True)
        self.assertEqual(propose_settings(self.root)["settings"]["unit_commands"],
                         [["{python}", "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"]])

    def test_detect_without_yes_only_proposes_and_writes_nothing(self):
        _python_repo(self.root)
        result = dispatch(parser().parse_args(["init", str(self.root), "--detect", "--agent", "claude"]))
        self.assertEqual(result["status"], "SETTINGS_PROPOSED")
        self.assertEqual(result["settings"]["module_roots"], ["app"])
        self.assertIn("--yes", result["next"])
        self.assertFalse((self.root / ".storyloop").exists())
        self.assertEqual(git(self.root, "status", "--porcelain").strip(), "")

    def test_detect_yes_locks_configures_the_agent_and_runs_readiness(self):
        _python_repo(self.root)
        result = dispatch(parser().parse_args(
            ["init", str(self.root), "--detect", "--yes", "--agent", "claude"]))
        self.assertEqual(result["status"], "BASELINE_LOCKED")
        self.assertEqual(result["agent"], "claude")
        self.assertIn(result["readiness"], {"READY", "READINESS_APPROVAL_REQUIRED"})
        self.assertIn("storyloop change", result["next"])
        config = json.loads((self.root / ".storyloop/config.json").read_text(encoding="utf-8"))
        self.assertEqual(config["adapter"], "command")
        self.assertEqual(config["agent_command"], ["{python}", "-m", "storyloop.claude_agent"])
        self.assertEqual(config["profile"]["module_roots"], ["app"])

    def test_agent_auto_prefers_claude_then_codex_and_fails_without_either(self):
        self.assertEqual(agent_config("auto", which=lambda name: "/bin/x")[0], "claude")
        self.assertEqual(agent_config("auto", which=lambda name: "/bin/x" if name == "codex" else None)[0], "codex")
        with self.assertRaises(StoryLoopError):
            agent_config("auto", which=lambda name: None)

    def test_detect_cannot_be_combined_with_explicit_settings(self):
        _python_repo(self.root)
        (self.root.parent / "s.json").write_text("{}", encoding="utf-8")
        with self.assertRaises(StoryLoopError):
            dispatch(parser().parse_args(["init", str(self.root), "--detect", "--settings",
                                          str(self.root.parent / "s.json")]))


if __name__ == "__main__":
    unittest.main()
