"""StoryLoop CLI 系统冒烟：在候选镜像中以子进程运行 CLI，检查版本与帮助输出。"""
from pathlib import Path
import subprocess
import sys
import unittest

from storyloop.testing.smoke_harness.v1 import HarnessError


def _cli(*args):
    try:
        child = subprocess.run([sys.executable, "-B", str(Path.cwd() / "storyloop_cli.py"), *args],
                               capture_output=True, text=True, timeout=60, cwd=Path.cwd())
    except (OSError, subprocess.TimeoutExpired) as error:
        raise HarnessError("CLI subprocess unavailable") from error
    if child.returncode != 0:
        raise HarnessError(f"CLI exited {child.returncode}")
    return child.stdout


class CliSmoke(unittest.TestCase):
    def test_version(self):
        self.assertRegex(_cli("--version").strip(), r"\d+\.\d+\.\d+")

    def test_help_lists_core_commands(self):
        out = _cli("--help")
        for command in ("init", "change", "approve", "run", "status", "base-propose"):
            self.assertIn(command, out)
