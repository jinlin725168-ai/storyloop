"""StoryLoop 自身 CLI 冒烟的行为失败与执行环境错误归因。"""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from storyloop.testing.smoke_harness.v1 import python_cases

ROOT = Path(__file__).resolve().parents[1]
CASE = "acceptance/smoke/test_cli.py"


class SmokeCliExitAttributionTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.project = Path(self.folder.name)
        target = self.project / CASE
        target.parent.mkdir(parents=True)
        shutil.copyfile(ROOT / CASE, target)

    def run_case(self):
        return python_cases(self.project, [CASE], cwd=self.project)

    def test_cli_nonzero_exit_is_behavior_failure(self):
        (self.project / "storyloop_cli.py").write_text(
            "raise SystemExit(3)\n", encoding="utf-8")
        result = self.run_case()
        self.assertEqual((result.code, result.failed, result.errors), (1, 2, 0))

    def test_cli_output_mismatch_is_behavior_failure(self):
        (self.project / "storyloop_cli.py").write_text(
            "print('unexpected output')\n", encoding="utf-8")
        result = self.run_case()
        self.assertEqual((result.code, result.failed, result.errors), (1, 2, 0))

    def test_cli_launch_and_timeout_are_environment_errors(self):
        for failure in (OSError("cannot start"),
                        subprocess.TimeoutExpired(["python"], 60)):
            with self.subTest(failure=type(failure).__name__):
                with patch("subprocess.run", side_effect=failure):
                    result = self.run_case()
                self.assertEqual((result.code, result.failed, result.errors), (2, 0, 2))
