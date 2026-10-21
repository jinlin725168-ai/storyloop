from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from storyloop.baseline import bootstrap_project
from storyloop.models import StoryLoopError
from storyloop.storage import locked_file, write_json
from tests.onboarding_support import cli, repository
from tests.test_baseline_behavior import git


class BootstrapTransactionTests(unittest.TestCase):
    def test_each_control_stage_rolls_back_only_its_own_writes(self):
        import storyloop.baseline as baseline
        import storyloop.system_smoke as smoke
        stages = ("exclude", "config.json", "baseline.json", "adoption", "adapters")
        for stage in stages:
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                repository(root)
                previous = (root / ".git/info/exclude").read_bytes()
                settings = {"adapters": {"manual": {"kind": "manual"}}} if stage == "adapters" else {}
                def fail_exclude(project):
                    original_exclude(project)
                    if stage == "exclude":
                        raise OSError("injected exclude failure")
                def fail_write(path, value, **kwargs):
                    write_json(path, value, **kwargs)
                    if path.name == stage:
                        raise OSError("injected write failure")
                def fail_adopt(project):
                    write_json(project / smoke.PROTECTION, {"version": 1, "digests": {}})
                    if stage == "adoption":
                        raise OSError("injected adoption failure")
                def fail_adapter(project):
                    write_json(project / ".storyloop/state/adapters.json", {"version": 1})
                    raise OSError("injected adapter failure")
                original_exclude = baseline._exclude_control_plane
                with patch.object(baseline, "_exclude_control_plane", fail_exclude), \
                        patch.object(baseline, "write_json", fail_write), \
                        patch.object(smoke, "adopt_system_smoke", fail_adopt), \
                        patch("storyloop.adapter_capabilities.check", fail_adapter):
                    with self.assertRaises((OSError, StoryLoopError)):
                        bootstrap_project(root, settings)
                self.assertEqual((root / ".git/info/exclude").read_bytes(), previous)
                self.assertFalse((root / ".storyloop").exists(), stage)

    def test_preexisting_control_state_is_restored_but_concurrent_edits_survive(self):
        for concurrent in (False, True):
            with self.subTest(concurrent=concurrent), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                repository(root)
                exclude = root / ".git/info/exclude"
                exclude.write_text(exclude.read_text() + "\n.storyloop/\n")
                config = root / ".storyloop/config.json"
                write_json(config, {"preexisting": True})
                before = config.read_bytes()
                def fail_after_adoption(project):
                    write_json(project / ".storyloop/state/system-smoke.json", {"version": 1})
                    if concurrent:
                        # An independent editor, deliberately outside the storage journal.
                        config.write_text('{"manual": true}')
                    raise OSError("injected adoption failure")
                with patch("storyloop.system_smoke.adopt_system_smoke", fail_after_adoption):
                    with self.assertRaises((OSError, StoryLoopError)) as caught:
                        bootstrap_project(root)
                self.assertEqual(config.read_bytes(), b'{"manual": true}' if concurrent else before)
                self.assertFalse((root / ".storyloop/baseline.json").exists())
                self.assertFalse((root / ".storyloop/state/system-smoke.json").exists())
                if concurrent:
                    self.assertIn("concurrent", str(caught.exception).lower())

    def test_exclusive_initialization_lock_rejects_second_initializer(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            with locked_file(root / ".git/storyloop-init.lock"):
                with self.assertRaisesRegex(StoryLoopError, "lock"):
                    bootstrap_project(root)
            self.assertFalse((root / ".storyloop").exists())


class ScaffoldTransactionTests(unittest.TestCase):
    def test_multi_file_failure_removes_only_new_unchanged_files(self):
        from storyloop.onboarding import scaffold_project
        for concurrent in (False, True):
            with self.subTest(concurrent=concurrent), tempfile.TemporaryDirectory() as folder:
                base = Path(folder)
                (base / "spelling").mkdir()
                root = base / "spelling/.."  # Different spelling, like Windows 8.3 temp paths.
                repository(root)
                original = Path.open
                def fail_second(path, mode="r", *args, **kwargs):
                    if mode == "xb" and path.resolve() == (root / "acceptance/AGENT_GUIDE.md").resolve():
                        if concurrent:
                            (root / "acceptance/tools/python_smoke.py").write_text("manual")
                        raise OSError("injected second install failure")
                    return original(path, mode, *args, **kwargs)
                with patch.object(Path, "open", fail_second):
                    with self.assertRaises((StoryLoopError, OSError)):
                        scaffold_project(root, ["python"])
                script = root / "acceptance/tools/python_smoke.py"
                if concurrent:
                    self.assertEqual(script.read_text(), "manual")
                else:
                    self.assertFalse(script.exists())
                    self.assertFalse((root / "acceptance").exists())
                self.assertFalse((root / "acceptance/onboarding.json").exists())

    def test_head_change_during_render_rejects_before_install(self):
        from storyloop.onboarding import scaffold_project
        from storyloop.onboarding_templates import render
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            def changed(stacks):
                artifacts = render(stacks)
                git(root, "commit", "--allow-empty", "-q", "-m", "concurrent")
                return artifacts
            with patch("storyloop.onboarding.render", changed):
                with self.assertRaisesRegex(StoryLoopError, "changed"):
                    scaffold_project(root, ["python"])
            self.assertFalse((root / "acceptance").exists())
