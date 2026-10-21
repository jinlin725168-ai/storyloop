"""Offline wheel/isolated installation check; uses only the already-installed build backend."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from tests.onboarding_support import repository
from tests.test_onboarding_lock import complete


class OnboardingPackageTests(unittest.TestCase):
    def test_wheel_contains_templates_and_installed_launcher_runs_outside_source(self):
        if importlib.util.find_spec("setuptools") is None:
            self.skipTest("build backend is not installed; no network installation performed")
        source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="installed 引擎 ") as folder:
            base = Path(folder)
            build, wheels, installed = base / "source", base / "wheels", base / "installed"
            build.mkdir()
            wheels.mkdir()
            for name in ("pyproject.toml", "README.md", "LICENSE"):
                shutil.copyfile(source / name, build / name)
            shutil.copytree(source / "storyloop", build / "storyloop", ignore=shutil.ignore_patterns("__pycache__"))
            self.run_command([sys.executable, "-I", "-c",
                              "from setuptools.build_meta import build_wheel; import sys; build_wheel(sys.argv[1])",
                              str(wheels)], build)
            wheel = next(wheels.glob("*.whl"))
            self.run_command([sys.executable, "-m", "pip", "install", "--no-index", "--no-deps",
                              "--target", str(installed), str(wheel)], base)
            project = base / "project"
            project.mkdir()
            repository(project)
            code = '''import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from storyloop.onboarding import scaffold_project
print(json.dumps(scaffold_project(Path.cwd(), ["python"])))
'''
            result = self.run_command([sys.executable, "-I", "-S", "-c", code, str(installed)], project)
            self.assertEqual(json.loads(result.stdout)["status"], "SCAFFOLD_CREATED")
            complete(project)
            launcher = installed / "storyloop/testing/smoke_harness/launcher.py"
            outcome = subprocess.run([sys.executable, "-I", "-S", str(launcher),
                                      "acceptance/tools/python_smoke.py", "tests/test_public.py"],
                                     cwd=project, capture_output=True, text=True)
            # The default case roots are acceptance-only: they must be completed explicitly.
            self.assertEqual(outcome.returncode, 2, outcome.stderr)
            path = project / "acceptance/onboarding.json"
            manifest = json.loads(path.read_text())
            manifest["case_roots"] = ["tests"]
            path.write_text(json.dumps(manifest))
            outcome = self.run_command([sys.executable, "-I", "-S", str(launcher),
                                        "acceptance/tools/python_smoke.py", "tests/test_public.py"], project)
            self.assertIn('"executed": 1', outcome.stdout)

    def run_command(self, argv, cwd):
        result = subprocess.run(argv, cwd=cwd, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr[-4000:])
        return result
