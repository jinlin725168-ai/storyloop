"""The suite runner: the core list stays real, and a failing module fails the whole run."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.suites import CORE

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "tests" / "run.py"


def _run(*args: str, root: Path = ROOT) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(RUNNER), "--root", str(root), *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


class SuiteListTests(unittest.TestCase):
    def test_every_core_module_exists(self):
        missing = [name for name in CORE if not (ROOT / (name.replace(".", "/") + ".py")).is_file()]
        self.assertEqual(missing, [])

    def test_core_is_a_strict_subset_of_full(self):
        core = _run("--suite", "core", "--list").stdout.split()
        full = _run("--suite", "full", "--list").stdout.split()
        self.assertEqual(core, CORE)
        self.assertLess(set(core), set(full))
        self.assertIn("tests.test_suites", full)


class RunnerTests(unittest.TestCase):
    def _project(self, modules: dict[str, str]) -> Path:
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (folder / "tests").mkdir()
        (folder / "tests" / "__init__.py").write_text("", encoding="utf-8")
        for name, body in modules.items():
            (folder / "tests" / f"{name}.py").write_text(
                "import unittest\n\nclass T(unittest.TestCase):\n    def test_it(self):\n        " + body + "\n",
                encoding="utf-8")
        return folder

    def test_all_green_exits_zero_and_counts_tests(self):
        root = self._project({"test_a": "pass", "test_b": "pass"})
        result = _run("--suite", "full", "--workers", "2", root=root)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("2 个模块，2 个测试", result.stdout)

    def test_one_failing_module_fails_the_run_and_shows_its_output(self):
        root = self._project({"test_a": "pass", "test_b": "self.assertEqual(1, 2)"})
        result = _run("--suite", "full", "--workers", "2", root=root)
        self.assertEqual(result.returncode, 1)
        self.assertIn("tests.test_b", result.stdout)
        self.assertIn("AssertionError: 1 != 2", result.stdout)

    def test_a_module_that_cannot_import_fails_the_run(self):
        root = self._project({"test_a": "pass"})
        (root / "tests" / "test_broken.py").write_text("import does_not_exist\n", encoding="utf-8")
        self.assertEqual(_run("--suite", "full", root=root).returncode, 1)


if __name__ == "__main__":
    unittest.main()
