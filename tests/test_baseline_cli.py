import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tests.test_baseline_behavior import repository


class BaselineCliTests(unittest.TestCase):
    def test_init_bootstraps_an_existing_clean_git_repository(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            result = subprocess.run(
                [sys.executable, "-m", "storyloop", "init", str(root), "--json"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["status"], "BASELINE_LOCKED")


if __name__ == "__main__":
    unittest.main()
