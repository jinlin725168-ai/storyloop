"""Affected-test selection adds the modules a change can break to a smaller suite."""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from affected import affected_modules  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def tree(root: Path, files: dict[str, str]) -> Path:
    for name, body in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return root


class AffectedTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = tree(Path(folder.name), {
            "storyloop/__init__.py": "",
            "storyloop/repair.py": "from .git_ops import commit\n",
            "storyloop/git_ops.py": "def commit(): pass\n",
            "storyloop/skills/review/SKILL.md": "---\nname: review\n---\n",
            "tests/support.py": "from storyloop.git_ops import commit\n",
            "tests/test_repair.py": "from storyloop.repair import x\n",
            "tests/test_relative.py": "from storyloop import repair\n",
            "tests/test_git.py": "import storyloop.git_ops\nfrom tests.support import commit\n",
            "tests/test_skill.py": "SKILL = 'storyloop/skills/review/SKILL.md'\n",
            "tests/test_other.py": "import os\n",
            "tests/fixtures/lifecycle/success.json": "{}",
            "tests/test_golden.py": "FIXTURES = 'fixtures/lifecycle'\n",
            "tests/test_pathlib.py": "FIXTURES = HERE / \"fixtures\" / \"lifecycle\"\n",
            "tests/test_ci_workflow.py": "WORKFLOW = '.github/workflows/ci.yml'\n",
            "tests/test_fixture_repo.py": "(root / '.github/workflows/ci.yml').write_text('x')\n",
        })

    def pick(self, *changed: str) -> list[str]:
        return affected_modules(self.root, list(changed))

    def test_source_change_selects_direct_importers_only(self):
        # #137 changed acceptance_repair.py; test_oracle_tiers imports it directly and broke
        # outside core for two days.
        self.assertEqual(self.pick("storyloop/repair.py"),
                         ["tests.test_relative", "tests.test_repair"])
        self.assertEqual(self.pick("storyloop/git_ops.py"), ["tests.test_git"])

    def test_changed_test_and_helper_modules(self):
        self.assertEqual(self.pick("tests/test_other.py"), ["tests.test_other"])
        self.assertEqual(self.pick("tests/support.py"), ["tests.test_git"])

    def test_data_files_select_tests_that_name_their_path(self):
        self.assertEqual(self.pick("storyloop/skills/review/SKILL.md"), ["tests.test_skill"])
        self.assertEqual(self.pick("tests/fixtures/lifecycle/success.json"),
                         ["tests.test_golden", "tests.test_pathlib"])
        self.assertEqual(self.pick(".github/workflows/ci.yml"), ["tests.test_ci_workflow"])

    def test_deleted_or_unrelated_files_select_nothing(self):
        self.assertEqual(self.pick("docs/guide.md", "storyloop/removed.py"), [])

    def test_runner_adds_affected_modules_to_a_suite(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "tests/run.py"), "--suite", "affected", "--list",
             "--changed", "storyloop/acceptance_repair.py"],
            capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("tests.test_oracle_tiers", result.stdout.split())
        core = subprocess.run(
            [sys.executable, str(ROOT / "tests/run.py"), "--suite", "core", "--list",
             "--changed", "storyloop/acceptance_repair.py"],
            capture_output=True, text=True, encoding="utf-8").stdout.split()
        self.assertIn("tests.test_oracle_tiers", core)
        self.assertEqual(len(core), len(set(core)))


if __name__ == "__main__":
    unittest.main()
