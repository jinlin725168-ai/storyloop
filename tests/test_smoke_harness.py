import json
import os
from pathlib import Path
import tempfile
import unittest

from storyloop import __version__
from storyloop.smoke_runner import run_commands


SCRIPT = '''from storyloop.testing.smoke_harness.v1 import python_cases

def run(context):
    return python_cases(context.project, context.cases)
'''


def fixture(root):
    tools = root / "acceptance/tools"
    tools.mkdir(parents=True)
    (tools / "python_smoke.py").write_text(SCRIPT, encoding="utf-8")
    value = {"version": 1, "template_version": 1, "engine_version": __version__, "helper_api": 1,
             "stacks": ["python"], "generated": {},
             "reviewed": {"settings": True, "guide": True},
             "case_roots": ["cases"], "executors": {
                 "acceptance/tools/python_smoke.py": {"stack": "python", "source_files": [],
                     "dependency_caches": [], "hooks": {
                         name: {"status": "implemented"} for name in
                         ("build", "start", "ready", "run_case", "cleanup")}}}}
    (root / "acceptance/onboarding.json").write_text(json.dumps(value), encoding="utf-8")
    (root / "cases").mkdir()
    for name, body in {"a.py": "self.assertEqual(1, 1)", "b.py": "self.fail('expected')",
                       "error.py": "raise RuntimeError('private fixture detail')"}.items():
        (root / "cases" / name).write_text(
            "import unittest\nclass Case(unittest.TestCase):\n    def test_case(self):\n        "
            + body + "\n", encoding="utf-8")
    (root / "cases/empty.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "cases/import.py").write_text("import no_such_fixture_dependency\n", encoding="utf-8")


def invoke(root, *cases):
    try:
        return run_commands(root, [["{storyloop_harness}", "acceptance/tools/python_smoke.py",
                                    *cases]], 15)["results"][0]
    except Exception as exc:
        return {"status": "UNAVAILABLE", "error_type": type(exc).__name__}


class HarnessTests(unittest.TestCase):
    def test_only_requested_case_runs_and_produces_real_counts(self):
        with tempfile.TemporaryDirectory(prefix="harness 空格 ") as folder:
            root = Path(folder)
            fixture(root)
            result = invoke(root, "cases/a.py")
            self.assertEqual(result["status"], "PASS", result)
            self.assertEqual(result["returncode"], 0)
            self.assertEqual(result["counts"]["executed"], 1)
            self.assertIn("-I", result["command"])
            self.assertTrue(Path(result["command"][0]).is_absolute())

    def test_strict_result_matrix(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fixture(root)
            rows = [((), 2), (("cases/b.py",), 1), (("cases/error.py",), 2),
                    (("cases/empty.py",), 2), (("cases/import.py",), 2),
                    (("cases/b.py", "cases/import.py"), 2),
                    (("cases/a.py", "cases/a.py"), 2), (("../a.py",), 2),
                    (("cases/missing.py",), 2), (("C:\\cases\\a.py",), 2),
                    (("acceptance/tools/python_smoke.py",), 2)]
            for cases, want in rows:
                with self.subTest(cases=cases):
                    result = invoke(root, *cases)
                    self.assertEqual(result.get("returncode"), want, result)
                    self.assertNotIn("private fixture detail", str(result))

    def test_candidate_package_and_pythonpath_cannot_shadow_trusted_helper(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fixture(root)
            fake = root / "storyloop"
            fake.mkdir()
            (fake / "__init__.py").write_text("raise RuntimeError('shadowed')\n")
            previous = os.environ.get("PYTHONPATH")
            os.environ["PYTHONPATH"] = str(root)
            try:
                result = invoke(root, "cases/a.py")
            finally:
                if previous is None:
                    os.environ.pop("PYTHONPATH")
                else:
                    os.environ["PYTHONPATH"] = previous
            self.assertEqual(result["status"], "PASS", result)

    def test_bad_version_and_linked_cases_are_environment_errors(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fixture(root)
            manifest = root / "acceptance/onboarding.json"
            value = json.loads(manifest.read_text())
            value["engine_version"] = "0.invalid"
            manifest.write_text(json.dumps(value))
            self.assertEqual(invoke(root, "cases/a.py").get("returncode"), 2)
            value["engine_version"] = __version__
            manifest.write_text(json.dumps(value))
            os.link(root / "cases/a.py", root / "cases/hard.py")
            self.assertEqual(invoke(root, "cases/hard.py").get("returncode"), 2)
            if os.name != "nt":
                (root / "cases/symbol.py").symlink_to(root / "cases/b.py")
                self.assertEqual(invoke(root, "cases/symbol.py").get("returncode"), 2)
