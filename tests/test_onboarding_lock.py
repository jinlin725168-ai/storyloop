import json
from pathlib import Path
import tempfile
import unittest

from storyloop.baseline import bootstrap_project, load_baseline, verify_baseline
from storyloop.models import StoryLoopError
from tests.onboarding_support import cli, commit, repository
from tests.test_baseline_behavior import git


def complete(root):
    cli("init", root, "--scaffold", "--stack", "python", "--json")
    path = root / "acceptance/onboarding.json"
    manifest = json.loads(path.read_text())
    manifest["reviewed"] = {"settings": True, "guide": True}
    executor = manifest["executors"]["acceptance/tools/python_smoke.py"]
    executor["hooks"] = {name: {"status": "not_applicable", "reason": "Pure Python file tests"}
                         for name in ("build", "start", "ready", "cleanup")}
    executor["hooks"]["run_case"] = {"status": "implemented"}
    path.write_text(json.dumps(manifest), encoding="utf-8")
    script = root / "acceptance/tools/python_smoke.py"
    script.write_text('''from storyloop.testing.smoke_harness.v1 import python_cases, run_hooks

def run_case(context, case):
    return python_cases(context.project, [case])

def run(context):
    return run_hooks(context, run_case=run_case)
''', encoding="utf-8")
    (root / "tests").mkdir(exist_ok=True)
    (root / "tests/test_public.py").write_text(
        "import unittest\nclass Public(unittest.TestCase):\n    def test_public(self):\n        self.assertEqual(1, 1)\n")
    settings = {"unit_commands": [["{python}", "-m", "unittest", "discover", "-s", "tests"]],
                "check_commands": [["{python}", "-c", "import ast; ast.parse(open('app/main.py').read())"]],
                "module_roots": ["app"], "test_roots": ["tests"], "integration_paths": []}
    (root / "acceptance/storyloop-settings.json").write_text(json.dumps(settings), encoding="utf-8")
    return settings


class OnboardingLockTests(unittest.TestCase):
    def test_committed_unfinished_scaffold_cannot_initialize(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            cli("init", root, "--scaffold", "--stack", "python", "--json")
            commit(root)
            settings = json.loads((root / "acceptance/storyloop-settings.json").read_text())
            with self.assertRaisesRegex(StoryLoopError, "review|pending|complete"):
                bootstrap_project(root, settings)
            self.assertFalse((root / ".storyloop").exists())

    def test_completed_committed_executor_is_locked_without_original_digest_match(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            settings = complete(root)
            commit(root)
            self.assertEqual(bootstrap_project(root, settings)["status"], "BASELINE_LOCKED")
            baseline = load_baseline(root)
            for path in ("acceptance/onboarding.json", "acceptance/AGENT_GUIDE.md",
                         "acceptance/storyloop-settings.json", "acceptance/tools/python_smoke.py"):
                self.assertIn(path, baseline["foundation_paths"])
                self.assertIn(path, baseline["protected_paths"])
            git(root, "switch", "-q", "-c", "feature/test-drift")
            (root / "acceptance/tools/python_smoke.py").write_text("# drift")
            with self.assertRaisesRegex(StoryLoopError, "Foundation drift"):
                verify_baseline(root, resume=True)

    def test_protected_artifact_overlap_is_rejected_before_control_writes(self):
        for key, paths in (("module_roots", ["acceptance"]), ("test_roots", ["acceptance/**"]),
                           ("module_roots", ["acceptance/tools/python_smoke.py"])):
            with self.subTest(key=key, paths=paths), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                repository(root)
                settings = complete(root)
                settings[key] = paths
                (root / "acceptance/storyloop-settings.json").write_text(json.dumps(settings))
                commit(root)
                with self.assertRaisesRegex(StoryLoopError, "protect|overlap"):
                    bootstrap_project(root, settings)
                self.assertFalse((root / ".storyloop").exists())

    def test_review_flags_do_not_hide_missing_hooks_or_fake_commands(self):
        for mutation in ("pending", "stub", "empty_commands", "changed_settings", "bad_version"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                repository(root)
                settings = complete(root)
                manifest_path = root / "acceptance/onboarding.json"
                manifest = json.loads(manifest_path.read_text())
                if mutation == "pending":
                    manifest["executors"]["acceptance/tools/python_smoke.py"]["hooks"]["build"] = {"status": "pending"}
                elif mutation == "stub":
                    (root / "acceptance/tools/python_smoke.py").write_text("def run_case(context, case):\n    pass\n")
                elif mutation == "empty_commands":
                    settings["unit_commands"] = []
                    (root / "acceptance/storyloop-settings.json").write_text(json.dumps(settings))
                elif mutation == "bad_version":
                    manifest["helper_api"] = 999
                manifest_path.write_text(json.dumps(manifest))
                commit(root)
                if mutation == "changed_settings":
                    settings["module_roots"] = ["different"]
                with self.assertRaises(StoryLoopError):
                    bootstrap_project(root, settings)
                self.assertFalse((root / ".storyloop").exists())
