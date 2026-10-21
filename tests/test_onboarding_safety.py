import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from storyloop.models import StoryLoopError
from storyloop.onboarding import scaffold_project
from tests.onboarding_support import cli, commit, repository
from tests.test_baseline_behavior import git
from tests.test_onboarding_lock import complete


class OnboardingSafetyTests(unittest.TestCase):
    def test_invalid_executor_syntax_is_environment_error_without_control_state(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            complete(root)
            (root / "acceptance/tools/python_smoke.py").write_text("def run(:\n")
            commit(root)
            code, output = cli("init", root, "--settings", root / "acceptance/storyloop-settings.json")
            self.assertEqual(code, 2, output)
            raw = json.dumps(output)
            self.assertIn("syntax", raw.lower())
            self.assertNotIn("Traceback", raw)
            self.assertNotIn("def run(:", raw)
            self.assertFalse((root / ".storyloop").exists())

    def test_linked_detection_marker_is_rejected_before_reading_it(self):
        if os.name == "nt":
            self.skipTest("symlink setup requires local developer privilege")
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            root = base / "repo"
            root.mkdir()
            repository(root)
            private = base / "private.json"
            private.write_text("{}")
            marker = root / "package.json"
            marker.symlink_to(private)
            commit(root)
            original = Path.open
            def deny_marker(path, *args, **kwargs):
                if path == marker:
                    self.fail("Linked detection marker was opened")
                return original(path, *args, **kwargs)
            with patch.object(Path, "open", deny_marker), self.assertRaises(StoryLoopError):
                scaffold_project(root, ["python"])

    def test_idempotent_drafts_do_not_hide_other_dirty_worktree_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            scaffold_project(root, ["python"])
            (root / "app/main.py").write_text("manual")
            with self.assertRaisesRegex(StoryLoopError, "changed|clean|dirty"):
                scaffold_project(root, ["python"])

    def test_head_change_after_staging_and_mid_install_rolls_back(self):
        import storyloop.onboarding_install as installation
        for during in ("stage", "write"):
            with self.subTest(during=during), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                repository(root)
                original_stage, original_open = installation._stage, Path.open
                def changed_stage(*args):
                    result = original_stage(*args)
                    if during == "stage":
                        git(root, "commit", "--allow-empty", "-q", "-m", "concurrent")
                    return result
                def changed_write(path, mode="r", *args, **kwargs):
                    if during == "write" and mode == "xb" and path.name == "python_smoke.py":
                        git(root, "commit", "--allow-empty", "-q", "-m", "concurrent")
                    return original_open(path, mode, *args, **kwargs)
                with patch.object(installation, "_stage", changed_stage), \
                        patch.object(Path, "open", changed_write):
                    with self.assertRaisesRegex(StoryLoopError, "changed"):
                        scaffold_project(root, ["python"])
                self.assertFalse((root / "acceptance").exists())

    def test_sensitive_sources_and_case_roots_cannot_cover_protected_executor(self):
        from storyloop.baseline import bootstrap_project
        for mode in ("secret", "case_root"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                repository(root)
                settings = complete(root)
                path = root / "acceptance/onboarding.json"
                manifest = json.loads(path.read_text())
                if mode == "secret":
                    manifest["executors"]["acceptance/tools/python_smoke.py"]["source_files"] = [".env"]
                else:
                    manifest["case_roots"] = ["acceptance"]
                path.write_text(json.dumps(manifest))
                commit(root)
                with self.assertRaises(StoryLoopError):
                    bootstrap_project(root, settings)
                self.assertFalse((root / ".storyloop").exists())

    def test_commit_between_validation_and_snapshot_is_rejected(self):
        import storyloop.onboarding_manifest as onboarding
        from storyloop.baseline import bootstrap_project
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            settings = complete(root)
            commit(root)
            original = onboarding.validate_onboarding
            def replace_reviewed_revision(project, profile):
                result = original(project, profile)
                path = root / "acceptance/onboarding.json"
                value = json.loads(path.read_text())
                value["executors"]["acceptance/tools/python_smoke.py"]["hooks"]["run_case"]["status"] = "pending"
                path.write_text(json.dumps(value))
                commit(root)
                return result
            with patch.object(onboarding, "validate_onboarding", replace_reviewed_revision):
                with self.assertRaisesRegex(StoryLoopError, "changed"):
                    bootstrap_project(root, settings)
            self.assertFalse((root / ".storyloop").exists())

    def test_boolean_versions_and_numeric_review_flags_are_rejected(self):
        from storyloop.baseline import bootstrap_project
        for field, value in (("version", True), ("template_version", True),
                             ("reviewed", {"settings": 1, "guide": 1})):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                repository(root)
                settings = complete(root)
                path = root / "acceptance/onboarding.json"
                manifest = json.loads(path.read_text())
                manifest[field] = value
                path.write_text(json.dumps(manifest))
                commit(root)
                with self.assertRaises(StoryLoopError):
                    bootstrap_project(root, settings)
                self.assertFalse((root / ".storyloop").exists())
