import importlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class CliTests(unittest.TestCase):
    def test_help_lists_product_and_qa_commands(self):
        self.assertIsNotNone(importlib.util.find_spec("storyloop.cli"), "CLI must exist")
        result = subprocess.run([sys.executable, "-m", "storyloop", "--help"],
                                capture_output=True, text=True, encoding="utf-8", check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        for command in ["discuss", "prepare", "approve", "run", "accept", "report", "demo"]:
            self.assertIn(command, result.stdout)

    def test_init_status_and_escaped_report(self):
        self.assertIsNotNone(importlib.util.find_spec("storyloop.cli"), "CLI must exist")
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder) / "project"
            init = subprocess.run([sys.executable, "-m", "storyloop", "init", str(project),
                                   "--goal", "Room <script>alert(1)</script>"],
                                  capture_output=True, text=True, encoding="utf-8", check=False)
            self.assertEqual(init.returncode, 0, init.stderr)
            report = subprocess.run([sys.executable, "-m", "storyloop", "report", str(project)],
                                    capture_output=True, text=True, encoding="utf-8", check=False)
            self.assertEqual(report.returncode, 0, report.stderr)
            html = (project / ".storyloop/report.html").read_text(encoding="utf-8")
            self.assertNotIn("<script>alert(1)</script>", html)
            self.assertIn("&lt;script&gt;", html)
            result = subprocess.run([sys.executable, "-m", "storyloop", "status", str(project), "--json"],
                                    capture_output=True, text=True, encoding="utf-8", check=False)
            self.assertIsInstance(json.loads(result.stdout), dict)


if __name__ == "__main__":
    unittest.main()
