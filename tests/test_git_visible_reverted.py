"""A file restored to its committed content is not an invisible write (2026-11-17 L3).

On sqlite-utils the implementer restored tests/test_cli.py to HEAD during an attempt; the
filesystem saw a change that Git could not, and the run stopped at WORKSPACE_TAINTED claiming
the implementation "created or modified ignored files".
"""

from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from storyloop.models import StoryLoopError
from storyloop.project_loop_guards import require_git_visible
from tests.test_baseline_behavior import git


class RevertedFileTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        git(self.root, "init", "-q")
        git(self.root, "config", "user.email", "storyloop@example.invalid")
        git(self.root, "config", "user.name", "StoryLoop Test")
        (self.root / ".gitignore").write_text("build/\n", encoding="utf-8")
        (self.root / "tests").mkdir()
        (self.root / "tests/test_cli.py").write_text("def test_x():\n    pass\n", encoding="utf-8")
        git(self.root, "add", ".")
        git(self.root, "commit", "-q", "-m", "base")
        self.empty = {"allowed": [], "violations": []}

    def test_restored_tracked_file_is_not_invisible(self):
        touched = [SimpleNamespace(path="tests/test_cli.py", kind="modified")]
        require_git_visible(touched, self.empty, project=self.root)

    def test_written_ignored_file_is_still_invisible(self):
        (self.root / "build").mkdir()
        (self.root / "build/out.txt").write_text("x", encoding="utf-8")
        touched = [SimpleNamespace(path="build/out.txt", kind="added")]
        with self.assertRaises(StoryLoopError) as caught:
            require_git_visible(touched, self.empty, project=self.root)
        self.assertEqual(caught.exception.code, "WORKSPACE_TAINTED")
        self.assertIn("build/out.txt", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
