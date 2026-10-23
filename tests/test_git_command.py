"""The extracted Git primitive keeps baseline's error and output contract."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from storyloop.errors import StoryLoopError
from storyloop.git_command import git
from storyloop.baseline import _git


class GitCommandTests(unittest.TestCase):
    def test_baseline_keeps_the_compatibility_alias(self):
        self.assertIs(_git, git)

    def test_empty_output_is_preserved(self):
        result = {"contained": True, "timed_out": False, "truncated": False,
                  "error": None, "returncode": 0, "stdout": "", "stderr": ""}
        with tempfile.TemporaryDirectory() as folder, patch(
            "storyloop.git_command.run_process", return_value=result
        ) as process:
            self.assertEqual(git(Path(folder), "status", "--porcelain"), "")
        self.assertEqual(process.call_args.args[2], 30)

    def test_git_failure_keeps_bounded_reason_and_code(self):
        result = {"contained": True, "timed_out": False, "truncated": False,
                  "error": None, "returncode": 128, "stdout": "", "stderr": "fatal: bad revision\n"}
        with tempfile.TemporaryDirectory() as folder, patch(
            "storyloop.git_command.run_process", return_value=result
        ):
            with self.assertRaises(StoryLoopError) as raised:
                git(Path(folder), "show", "missing")
        self.assertEqual(raised.exception.code, "GIT_BLOCKED")
        self.assertEqual(str(raised.exception), "Git operation failed: fatal: bad revision")

    def test_uncontained_result_fails_safely(self):
        with tempfile.TemporaryDirectory() as folder, patch(
            "storyloop.git_command.run_process", return_value={"contained": False}
        ):
            with self.assertRaises(StoryLoopError) as raised:
                git(Path(folder), "status")
        self.assertEqual(raised.exception.code, "GIT_BLOCKED")
        self.assertEqual(str(raised.exception), "Git process failed safely")
