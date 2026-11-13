"""Editable candidate installs must not enter the trusted harness interpreter."""

import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest
import venv

from storyloop.approvals import approve_change
from storyloop.baseline import bootstrap_project
from storyloop.bench_worker import _run_until_stop, _stop_detail
from storyloop.models import StoryLoopError
from storyloop.project_loop import load_run_state, run_change
from storyloop.testing.smoke_harness.results import PREFIX
from tests.routing_support import legacy_create_change
from tests.support import product
from tests.test_baseline_behavior import repository
from tests.test_project_loop import smoke_payload
from tests.test_smoke_harness import fixture
from tests.test_smoke_repair import services


class EditableHarnessTests(unittest.TestCase):
    def _venv(self, root: Path) -> tuple[Path, Path]:
        environment = root / "venv"
        venv.EnvBuilder(with_pip=False).create(environment)
        python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        site = subprocess.check_output(
            [python, "-c", "import site; print(site.getsitepackages()[0])"], text=True).strip()
        return python, Path(site)

    def _launch(self, python: Path, project: Path, *arguments: str) -> dict:
        launcher = Path(__file__).resolve().parents[1] / "storyloop/testing/smoke_harness/launcher.py"
        result = subprocess.run([python, "-I", "-B", str(launcher), *arguments],
                                cwd=project, capture_output=True, text=True)
        self.assertTrue(result.stdout.startswith(PREFIX), result.stderr)
        return json.loads(result.stdout[len(PREFIX):])

    def test_candidate_pth_path_does_not_block_protected_case(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            project = root / "project"
            project.mkdir()
            fixture(project)
            (project / "candidate_local.py").write_text("VALUE = 1\n")
            (project / "cases/a.py").write_text(
                "import sys, unittest\n"
                "class Case(unittest.TestCase):\n"
                "    def test_case(self):\n"
                "        self.assertNotIn('candidate_local', sys.modules)\n")
            python, site = self._venv(root)
            (site / "candidate.pth").write_text(str(project) + "\nimport candidate_local\n")
            passed = self._launch(python, project, "acceptance/tools/python_smoke.py", "cases/a.py")
            failed = self._launch(python, project, "acceptance/tools/python_smoke.py", "cases/b.py")
            self.assertEqual((passed["code"], passed["executed"]), (0, 1))
            self.assertEqual((failed["code"], failed["failed"]), (1, 1))

    def test_editable_finder_for_candidate_is_removed_before_trusted_import(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            project = root / "project"
            project.mkdir()
            fixture(project)
            candidate_package = project / "storyloop"
            candidate_package.mkdir()
            (candidate_package / "__init__.py").write_text("raise RuntimeError('candidate imported')\n")
            (project / "cases/a.py").write_text(
                "import sys, unittest\n"
                "class Case(unittest.TestCase):\n"
                "    def test_case(self):\n"
                "        self.assertFalse(any(type(f).__module__.startswith('__editable__') "
                "for f in sys.meta_path))\n")
            python, site = self._venv(root)
            finder = (
                "import importlib.util, sys\n"
                f"MAPPING = {{'storyloop': {str(candidate_package)!r}}}\n"
                "class Finder:\n"
                "    def find_spec(self, fullname, path=None, target=None):\n"
                "        if fullname == 'storyloop':\n"
                "            return importlib.util.spec_from_file_location(fullname, "
                "MAPPING['storyloop'] + '/__init__.py')\n"
                "def install(): sys.meta_path.insert(0, Finder())\n")
            (site / "__editable___candidate_finder.py").write_text(finder)
            (site / "candidate.pth").write_text(
                "import __editable___candidate_finder; __editable___candidate_finder.install()\n")
            result = self._launch(python, project, "acceptance/tools/python_smoke.py", "cases/a.py")
            self.assertEqual((result["code"], result["executed"]), (0, 1), result)

    def test_launcher_error_includes_bounded_case_diagnostic(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            project = root / "project"
            project.mkdir()
            fixture(project)
            python, _ = self._venv(root)
            result = self._launch(python, project, "missing-executor", "cases/a.py")
            self.assertEqual(result["code"], 2)
            diagnostic = next(item for item in result["diagnostics"] if isinstance(item, dict))
            self.assertEqual((diagnostic["case_id"], diagnostic["kind"]), ("launcher", "error"))
            self.assertIn("Executor is not declared", diagnostic["message"])
            self.assertLessEqual(len(diagnostic["message"]), 2000)

    def test_opaque_editable_finder_reports_explicit_isolation_error(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            project = root / "project"
            project.mkdir()
            fixture(project)
            python, site = self._venv(root)
            (site / "__editable___opaque_finder.py").write_text(
                "import sys\nclass Finder:\n"
                "    def find_spec(self, fullname, path=None, target=None): return None\n"
                "def install(): sys.meta_path.insert(0, Finder())\n")
            (site / "opaque.pth").write_text(
                "import __editable___opaque_finder; __editable___opaque_finder.install()\n")
            result = self._launch(python, project, "acceptance/tools/python_smoke.py", "cases/a.py")
            self.assertEqual(result["code"], 2)
            self.assertIn("ImportIsolationError: Editable import finder cannot be isolated",
                          str(result["diagnostics"]))

    def test_preloaded_candidate_storyloop_keeps_untrusted_installation_error(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            project = root / "project"
            project.mkdir()
            fixture(project)
            candidate_package = project / "storyloop"
            candidate_package.mkdir()
            (candidate_package / "__init__.py").write_text("\n")
            python, site = self._venv(root)
            (site / "candidate.pth").write_text(
                f"import sys; sys.path.insert(0, {str(project)!r}); import storyloop\n")
            result = self._launch(python, project, "acceptance/tools/python_smoke.py", "cases/a.py")
            self.assertEqual(result["code"], 2)
            self.assertIn("Untrusted helper installation", str(result["diagnostics"]))

    def test_red_environment_error_is_persisted_with_evidence_and_retry(self):
        with tempfile.TemporaryDirectory(prefix="red 环境 ") as folder:
            project = Path(folder)
            repository(project)
            bootstrap_project(project, {"test_commands": []})
            change_id = legacy_create_change(project, "orders", product())["change_id"]
            approve_change(project, change_id)
            payload = smoke_payload(change_id)
            payload["files"][0]["content"] = (
                "import time\nprint('RED launch diagnostic', flush=True)\ntime.sleep(3)\n")
            payload["manifest"]["tests"][0]["timeout_seconds"] = 1
            result = run_change(project, change_id, services(change_id, lambda *_: payload))
            state = load_run_state(project, change_id)
            red = project / ".storyloop/evidence" / change_id / "acceptance-red.json"
            self.assertEqual((result["status"], state["status"]), ("NEEDS_INPUT", "NEEDS_INPUT"))
            self.assertTrue(red.is_file())
            self.assertIn("RED launch diagnostic", str(json.loads(red.read_text())["results"]))
            self.assertIn("RED launch diagnostic", str(state["stop"]["evidence"]))
            self.assertIn("storyloop run", state["stop"]["resume"])
            self.assertIn("storyloop run", state["stop"]["options"][0]["command"])
            command_project = Path(shlex.split(state["stop"]["options"][0]["command"])[2])
            self.assertTrue(command_project.samefile(project))
            again = run_change(project, change_id, services(change_id, lambda *_: payload))
            self.assertEqual(again["status"], "NEEDS_INPUT")
            self.assertIn("RED launch diagnostic", str(again["stop"]["evidence"]))

    def test_bench_stop_detail_keeps_exception_details(self):
        result = {"error": "RED failed", "details": {"results": [
            {"diagnostics": [{"message": "token=private launch"}]}]}}
        detail = _stop_detail({"status": "CONTRACT_COMMITTED"}, result)
        self.assertIn("[REDACTED]", str(detail))
        self.assertIn("results", str(detail))

    def test_bench_exception_details_reach_stopped_attempt(self):
        def fail(*_):
            raise StoryLoopError("RED failed", details={"results": [{"stderr": "launch clue"}]})

        outcome = _run_until_stop(Path("/tmp/fixture"), "FEAT-20261110-x", fail,
                                  lambda *_: None, lambda *_: {"status": "NEEDS_INPUT"})
        self.assertEqual(outcome["status"], "NEEDS_HUMAN")
        self.assertIn("launch clue", str(outcome["stops"][0]["detail"]))


if __name__ == "__main__":
    unittest.main()
