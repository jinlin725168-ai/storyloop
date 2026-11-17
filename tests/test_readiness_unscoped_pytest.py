"""A unit command must not collect the protected acceptance suites (2026-11-16 L3).

On sqlite-utils the unit layer was `python -m pytest -q`, which also collected
acceptance/changes/*/test_*.py; those must fail before implementation, so the
characterization gate could never pass.
"""

from pathlib import Path
import tempfile
import unittest

from storyloop.readiness import inspect_readiness, unscoped_pytest
from tests.routing_support import readiness_repository


class UnscopedPytestTests(unittest.TestCase):
    def test_pytest_without_paths_or_ignore_is_unscoped(self):
        for command in (["{python}", "-m", "pytest", "-q"],
                        ["{python}", "-m", "pytest", "-q", "-p", "no:cacheprovider"],
                        ["pytest", "-x", "-k", "tables"],
                        ["/venv/bin/pytest"], ["{python}", "-m", "pytest", "."]):
            with self.subTest(command=command):
                self.assertTrue(unscoped_pytest(command, configured_testpaths=False))
        for command in (["{python}", "-m", "pytest", "-q", "tests"],
                        ["pytest", "tests/test_cli.py"],
                        ["{python}", "-m", "pytest", "--ignore=acceptance"],
                        ["{python}", "-m", "pytest", "--ignore", "acceptance"],
                        ["{python}", "-m", "unittest", "discover", "-s", "tests"]):
            with self.subTest(command=command):
                self.assertFalse(unscoped_pytest(command, configured_testpaths=False))
        self.assertFalse(unscoped_pytest(["pytest", "-q"], configured_testpaths=True))

    def test_readiness_blocks_an_unscoped_pytest_unit_layer_with_the_fix(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            readiness_repository(root, unit_commands=[["{python}", "-m", "pytest", "-q"]])
            record = inspect_readiness(root, refresh=True)
        unit = record["layers"]["unit"]
        self.assertEqual(unit["status"], "FAILED")
        self.assertIn("unit", record["blockers"])
        self.assertIn("acceptance", str(unit["results"]))
        self.assertIn("tests", str(unit["results"]))


if __name__ == "__main__":
    unittest.main()
