import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.onboarding_support import cli, repository
from tests.test_baseline_behavior import git


class ScaffoldTests(unittest.TestCase):
    def test_scaffold_only_writes_drafts_and_rejects_unfinished_executor(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            head = git(root, "rev-parse", "HEAD")
            exclude = (root / ".git/info/exclude").read_bytes()
            code, result = cli("init", root, "--scaffold", "--stack", "python", "--json")
            self.assertEqual(code, 0, result)
            self.assertEqual(result["status"], "SCAFFOLD_CREATED")
            self.assertFalse(result["initialized"])
            self.assertIn("人工补全", result["next"])
            manifest = json.loads((root / "acceptance/onboarding.json").read_text())
            self.assertEqual(manifest["stacks"], ["python"])
            self.assertEqual(set(manifest["generated"]), {
                "acceptance/tools/python_smoke.py", "acceptance/AGENT_GUIDE.md",
                "acceptance/storyloop-settings.json"})
            self.assertEqual(git(root, "rev-parse", "HEAD"), head)
            self.assertEqual((root / ".git/info/exclude").read_bytes(), exclude)
            self.assertFalse((root / ".storyloop").exists())
            from storyloop.smoke_runner import run_commands
            run = run_commands(root, [["{storyloop_harness}",
                                      "acceptance/tools/python_smoke.py"]], 10)
            self.assertEqual(run["results"][0]["returncode"], 2)
            self.assertEqual(run["results"][0]["status"], "ERROR")


if __name__ == "__main__":
    unittest.main()


class ScaffoldMatrixTests(unittest.TestCase):
    def test_package_and_generated_checkout_line_endings_preserve_idempotence(self):
        bundled = Path(__file__).resolve().parents[1] / "storyloop/templates/onboarding"
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            resources = base / "resources"
            templates = resources / "templates/onboarding"
            templates.mkdir(parents=True)
            for name in ("AGENT_GUIDE.md", "executor.py.tmpl"):
                content = (bundled / name).read_text(encoding="utf-8")
                (templates / name).write_bytes(content.replace("\n", "\r\n").encode("utf-8"))
            root = base / "repo"
            root.mkdir()
            repository(root)
            args = ("init", root, "--scaffold", "--stack", "python", "--json")
            with patch("storyloop.onboarding_templates.files", return_value=resources):
                self.assertEqual(cli(*args)[0], 0)
                code, value = cli(*args)
                self.assertEqual(code, 0, value)
                self.assertEqual(value["status"], "SCAFFOLD_UNCHANGED")
                for relative in value["files"]:
                    path = root / relative
                    content = path.read_bytes().replace(b"\r\n", b"\n")
                    path.write_bytes(content.replace(b"\n", b"\r\n"))
                code, value = cli(*args)
                self.assertEqual(code, 0, value)
                self.assertEqual(value["status"], "SCAFFOLD_UNCHANGED")

    def test_explicit_stack_matrix_and_ambiguous_selection(self):
        markers = {"maven": {"pom.xml": "<project/>"},
                   "node": {"package.json": '{"scripts": {"test": "not executed"}}'},
                   "python": {"pyproject.toml": "[project]"},
                   "generic": {"build.gradle": "// fixture"}}
        for stacks in (["maven"], ["node"], ["python"], ["maven", "node"], ["generic"]):
            with self.subTest(stacks=stacks), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                repository(root, {k: v for name in stacks for k, v in markers[name].items()})
                args = [arg for stack in stacks for arg in ("--stack", stack)]
                code, value = cli("init", root, "--scaffold", *args, "--json")
                self.assertEqual(code, 0, value)
                for stack in stacks:
                    self.assertTrue((root / f"acceptance/tools/{stack}_smoke.py").is_file())
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root, {"pom.xml": "<project/>", "package.json": "{}"})
            code, value = cli("init", root, "--scaffold", "--json")
            self.assertEqual(code, 2)
            self.assertEqual(value["status"], "SCAFFOLD_SELECTION_REQUIRED")
            self.assertEqual(set(value["suggested_stacks"]), {"maven", "node"})
            self.assertFalse((root / "acceptance").exists())

    def test_idempotent_unmodified_draft_but_manual_edits_are_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            args = ("init", root, "--scaffold", "--stack", "python", "--json")
            self.assertEqual(cli(*args)[0], 0)
            path = root / "acceptance/AGENT_GUIDE.md"
            before = path.stat().st_mtime_ns
            code, value = cli(*args)
            self.assertEqual(code, 0, value)
            self.assertEqual(value["status"], "SCAFFOLD_UNCHANGED")
            self.assertEqual(path.stat().st_mtime_ns, before)
            path.write_text("manual review", encoding="utf-8")
            self.assertEqual(cli(*args)[0], 2)
            self.assertEqual(path.read_text(), "manual review")

    def test_conflicting_target_and_existing_baseline_are_refused(self):
        from tests.onboarding_support import commit
        from storyloop.baseline import bootstrap_project
        for baseline in (False, True):
            with self.subTest(baseline=baseline), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                repository(root)
                if baseline:
                    bootstrap_project(root)
                else:
                    (root / "acceptance").mkdir()
                    (root / "acceptance/AGENT_GUIDE.md").write_text("existing")
                    commit(root)
                code, value = cli("init", root, "--scaffold", "--stack", "python", "--json")
                self.assertEqual(code, 2, value)
                self.assertFalse((root / "acceptance/onboarding.json").exists())
