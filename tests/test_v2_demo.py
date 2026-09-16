"""Executable two-adapter v2 demo smoke test."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path


class V2DemoTests(unittest.TestCase):
    def test_demo_runs_both_command_adapters_to_project_green(self):
        script = Path(__file__).parents[1] / "examples/v2-demo/run_demo.py"
        result = subprocess.run(
            [sys.executable, str(script)], cwd=Path(__file__).parents[1],
            capture_output=True, text=True, encoding="utf-8", check=False,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual([item["adapter"] for item in value["runs"]],
                         ["agent-a", "agent-b"])
        self.assertTrue(all(item["status"] == "PROJECT_GREEN"
                            for item in value["runs"]))
        self.assertTrue(all(item["metrics"][0]["attempt"] == 1
                            for item in value["runs"]))


if __name__ == "__main__":
    unittest.main()
