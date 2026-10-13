from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from storyloop.baseline import bootstrap_project, load_baseline, verify_baseline
from storyloop.models import StoryLoopError


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        [shutil.which("git") or "git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if result.returncode:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


def repository(root: Path) -> None:
    git(root, "init", "-q")
    git(root, "config", "user.email", "storyloop@example.invalid")
    git(root, "config", "user.name", "StoryLoop Test")
    (root / "pom.xml").write_text("<project/>\n", encoding="utf-8")
    (root / ".github/workflows").mkdir(parents=True)
    (root / ".github/workflows/ci.yml").write_text("name: existing\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "initial")


class BaselineBehaviorTests(unittest.TestCase):
    def test_bootstrap_records_tracked_files_and_leaves_tree_clean(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            ci_before = (root / ".github/workflows/ci.yml").read_bytes()

            result = bootstrap_project(root)
            baseline = load_baseline(root)

            self.assertEqual(result["status"], "BASELINE_LOCKED")
            self.assertEqual(baseline["source_head"], git(root, "rev-parse", "HEAD"))
            self.assertEqual(baseline["source_branch"], git(root, "branch", "--show-current"))
            self.assertEqual(baseline["baseline_version"], "1.0.0")
            self.assertEqual(len(baseline["files"]["pom.xml"]), 64)
            self.assertIn(".github/workflows/ci.yml", baseline["protected_paths"])
            self.assertEqual(git(root, "status", "--porcelain=v1", "--untracked-files=all"), "")
            self.assertEqual((root / ".github/workflows/ci.yml").read_bytes(), ci_before)

    def test_dirty_or_non_git_project_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with self.assertRaisesRegex(StoryLoopError, "Git"):
                bootstrap_project(root)

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            (root / "dirty.txt").write_text("dirty", encoding="utf-8")
            with self.assertRaisesRegex(StoryLoopError, "clean"):
                bootstrap_project(root)


    def test_tests_merged_by_an_earlier_change_do_not_block_the_next_one(self):
        """Found in real acceptance: tests a merged change added under test_roots (outside module_roots)
        made every later change refuse to start."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            bootstrap_project(root, {"test_commands": [], "module_roots": ["app"], "test_roots": ["tests"]})
            (root / "tests").mkdir()
            (root / "tests/test_added.py").write_text("pass\n", encoding="utf-8")
            git(root, "add", "."); git(root, "commit", "-q", "-m", "merged change")
            self.assertEqual(verify_baseline(root)["status"], "BASELINE_VERIFIED")
            (root / "stray.py").write_text("x = 1\n", encoding="utf-8")
            git(root, "add", "."); git(root, "commit", "-q", "-m", "outside every root")
            with self.assertRaisesRegex(StoryLoopError, "outside feature modules"):
                verify_baseline(root)

if __name__ == "__main__":
    unittest.main()
