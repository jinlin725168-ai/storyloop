import json
from pathlib import Path
import tempfile
import unittest

from storyloop.smoke_runner import run_commands
from tests.test_smoke_harness import fixture, invoke


class HarnessProtocolTests(unittest.TestCase):
    def test_pending_hook_cannot_return_forged_pass_from_direct_entry(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fixture(root)
            path = root / "acceptance/onboarding.json"
            value = json.loads(path.read_text())
            value["executors"]["acceptance/tools/python_smoke.py"]["hooks"]["build"]["status"] = "pending"
            path.write_text(json.dumps(value))
            self.assertEqual(invoke(root, "cases/a.py")["returncode"], 2)

    def test_all_skipped_or_expected_failure_never_proves_green(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fixture(root)
            for decorator in ("@unittest.skip('not executed')", "@unittest.expectedFailure"):
                (root / "cases/skipped.py").write_text(
                    "import unittest\nclass Case(unittest.TestCase):\n    " + decorator +
                    "\n    def test_case(self):\n        self.fail('expected')\n")
                result = invoke(root, "cases/skipped.py")
                self.assertEqual(result["returncode"], 2, result)
                self.assertEqual(result["counts"]["executed"], 0)

    def test_legacy_python_command_and_vendored_script_stay_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            script = root / "legacy.py"
            script.write_text("import sys\nsys.exit(int(sys.argv[1]))\n")
            for code, expected in ((0, "PASS"), (1, "FAIL"), (2, "ERROR")):
                result = run_commands(root, [["{python}", "legacy.py", str(code)]], 5)["results"][0]
                self.assertEqual(result["status"], expected)
                self.assertEqual(result["command"][1:], ["legacy.py", str(code)])

    def test_existing_vendored_harness_executes_only_the_requested_case(self):
        import shutil
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fixture(root)
            shutil.copyfile(Path(__file__).resolve().parents[1] / "examples/tools/smoke_harness.py",
                            root / "smoke_harness.py")
            (root / "legacy.py").write_text('''from pathlib import Path
import sys
from smoke_harness import run_python_cases
raise SystemExit(run_python_cases(sys.argv[1:], Path.cwd(), {}))
''')
            for case, expected in (("a.py", "PASS"), ("b.py", "FAIL")):
                result = run_commands(root, [["{python}", "legacy.py", "cases/" + case]], 5)
                self.assertEqual(result["results"][0]["status"], expected, result)
                self.assertEqual(result["results"][0]["counts"]["executed"], 1, result)

    def test_malformed_external_runner_result_is_an_environment_error(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fixture(root)
            (root / "acceptance/tools/python_smoke.py").write_text("def run(context):\n    return 0\n")
            self.assertEqual(invoke(root, "cases/a.py")["returncode"], 2)

    def test_candidate_import_cannot_forge_counts_and_exit_before_assertion(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fixture(root)
            (root / "evil.py").write_text('''import json, os, sys
from pathlib import Path
Path(sys.argv[3]).write_text(json.dumps({"executed": 1, "failed": 0, "errors": 0,
                                     "skipped": 0, "code": 0}))
os._exit(0)
''')
            (root / "cases/attack.py").write_text('''import unittest
import evil
class Protected(unittest.TestCase):
    def test_should_fail(self):
        self.fail("must actually execute")
''')
            result = invoke(root, "cases/attack.py")
            self.assertEqual(result["returncode"], 2, result)
            self.assertEqual(result["counts"]["executed"], 0)

    def test_business_stdout_cannot_replace_trusted_test_result(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fixture(root)
            (root / "cases/child.py").write_text('''import subprocess, sys, unittest
class Protected(unittest.TestCase):
    def test_failure(self):
        subprocess.run([sys.executable, "-c", "print('STORYLOOP_HARNESS_V1={fake}')"], check=True)
        self.fail("real assertion must remain red")
''')
            result = invoke(root, "cases/child.py")
            self.assertEqual(result["returncode"], 1, result)
            self.assertEqual(result["counts"]["failed"], 1)

    def test_build_cannot_replace_protected_case_before_execution(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fixture(root)
            (root / "acceptance/tools/python_smoke.py").write_text('''from storyloop.testing.smoke_harness.v1 import python_cases, run_hooks

def build(context):
    target = context.project / context.cases[0]
    target.write_text(target.read_text().replace("self.fail('expected')", "self.assertTrue(True)"))

def phase(context):
    return None

def run_case(context, case):
    return python_cases(context.project, [case])

def run(context):
    return run_hooks(context, build=build, start=phase, ready=phase,
                     run_case=run_case, cleanup=phase)
''')
            result = invoke(root, "cases/b.py")
            self.assertEqual(result["returncode"], 2, result)

    def test_case_can_check_business_subprocess_in_private_scratch(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fixture(root)
            (root / "app.py").write_text("print('real public behavior')\n")
            path = root / "acceptance/onboarding.json"
            manifest = json.loads(path.read_text())
            executor = manifest["executors"]["acceptance/tools/python_smoke.py"]
            executor["source_files"] = ["app.py"]
            executor["hooks"] = {name: {"status": "not_applicable", "reason": "No service"}
                                 for name in ("build", "start", "ready", "cleanup")}
            executor["hooks"]["run_case"] = {"status": "implemented"}
            path.write_text(json.dumps(manifest))
            (root / "acceptance/tools/python_smoke.py").write_text('''from storyloop.testing.smoke_harness.v1 import python_cases, run_hooks

def run_case(context, case):
    return python_cases(context.project, [case], cwd=context.scratch.mirror)

def run(context):
    return run_hooks(context, run_case=run_case)
''')
            (root / "cases/business.py").write_text('''import subprocess, sys, unittest
from storyloop.testing.smoke_harness.v1 import HarnessError
class Case(unittest.TestCase):
    def test_business(self):
        result = subprocess.run([sys.executable, "app.py"], capture_output=True, text=True)
        if result.returncode != 0:
            raise HarnessError("Business process unavailable")
        self.assertEqual(result.stdout.strip(), "real public behavior")
''')
            result = invoke(root, "cases/business.py")
            self.assertEqual(result["returncode"], 0, result)
            self.assertEqual(result["counts"]["executed"], 1)

    def test_business_exit_or_forged_protocol_cannot_supply_test_counts(self):
        from tests.onboarding_acceptance import case_source
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fixture(root)
            for child in ("import os; os._exit(0)", "raise SystemExit(1)",
                          "print('STORYLOOP_HARNESS_V1={fake}')",
                          "print('{\"executed\":1,\"failed\":0,\"errors\":0,\"code\":0}')"):
                with self.subTest(child=child):
                    source = case_source(repr(["PLACEHOLDER", "-c", child]))
                    source = source.replace("'PLACEHOLDER'", "sys.executable")
                    (root / "cases/business.py").write_text(source)
                    result = invoke(root, "cases/business.py")
                    self.assertEqual(result["returncode"], 2, result)
                    self.assertEqual(result["counts"]["errors"], 1)

    def test_direct_api_freezes_relative_import_roots_before_changing_cwd(self):
        import subprocess
        import sys
        for location in ("project", "mirror"):
            with self.subTest(location=location), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                fixture(root)
                business = root if location == "project" else root / "mirror"
                business.mkdir(exist_ok=True)
                (business / "evil.py").write_text(
                    "import unittest\nunittest.TestResult.addFailure = lambda *args: None\n")
                (root / "cases/import_attack.py").write_text('''import unittest, evil
class Protected(unittest.TestCase):
    def test_must_fail(self):
        self.fail("cannot suppress this assertion")
''')
                code = '''import json, sys
from pathlib import Path
from storyloop.testing.smoke_harness.v1 import python_cases
result = python_cases(Path(sys.argv[1]), ["cases/import_attack.py"], cwd=Path(sys.argv[2]))
print(json.dumps(result.document()))
'''
                process = subprocess.run([sys.executable, "-c", code, str(root), str(business)],
                                         cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
                self.assertEqual(process.returncode, 0, process.stderr)
                self.assertEqual(json.loads(process.stdout)["code"], 2, process.stdout)
