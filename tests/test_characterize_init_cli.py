"""Preserved public CLI behavior for initialization preflight failures."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tests.lesson_support import cleanup_repo, git_repo


def init_result(project: Path) -> tuple[subprocess.CompletedProcess, dict]:
    result = subprocess.run(
        [sys.executable, "-m", "storyloop", "init", str(project)],
        capture_output=True, text=True, check=False,
    )
    return result, json.loads(result.stderr)


class InitCliCharacterizationTests(unittest.TestCase):
    def test_preflight_failures_keep_messages_and_exit_code(self):
        with tempfile.TemporaryDirectory() as folder:
            non_git = Path(folder) / "not a git repo"
            non_git.mkdir()
            dirty = git_repo()
            self.addCleanup(cleanup_repo, dirty)
            (dirty / "uncommitted.txt").write_text("dirty\n", encoding="utf-8")

            cases = (
                (non_git, "storyloop init needs an existing Git repository (git init first)"),
                (dirty, "Git worktree must be clean before initialization"),
            )
            for project, expected_message in cases:
                with self.subTest(project=project):
                    result, payload = init_result(project)
                    self.assertEqual(result.returncode, 2)
                    self.assertEqual(payload["message"], expected_message)


if __name__ == "__main__":
    unittest.main()
