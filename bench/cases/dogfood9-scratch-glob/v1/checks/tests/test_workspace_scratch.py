"""Agents may touch declared scratch paths and refresh .git/index without tainting the run."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import subprocess
import tempfile
import unittest

import json
import os

from storyloop.approvals import approve_change
from storyloop.boundaries import capture_snapshot, snapshot_changes
from storyloop.baseline import bootstrap_project
from tests.routing_support import legacy_create_change as create_change
from storyloop.models import StoryLoopError
from storyloop.project_loop import LoopServices, run_change
from storyloop.project_loop_guards import _is_scratch
from tests.support import product
from tests.test_baseline_behavior import git, repository
from tests.test_project_loop import smoke_payload

NOW = datetime(2026, 9, 17, tzinfo=timezone.utc)


def _approved(root: Path, settings: dict) -> str:
    repository(root)
    (root / ".gitignore").write_text("target/\n*.egg-info/\n", encoding="utf-8")
    git(root, "add", ".gitignore")
    git(root, "commit", "-q", "-m", "ignore build output")
    bootstrap_project(root, {"test_commands": [], **settings})
    state = create_change(root, "orders", product(), now=lambda: NOW)
    approve_change(root, state["change_id"], now=lambda: NOW)
    return state["change_id"]


def _services(change_id: str, build_in_place: bool) -> LoopServices:
    def implement(project, context):
        target = project / "modules/orders/service.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("VALUE = 1\n", encoding="utf-8")
        if build_in_place:
            (project / "target").mkdir(exist_ok=True)
            (project / "target/App.class").write_bytes(b"\x00")
            # Read-only git rewrites .git/index in real checkouts (stat cache refresh); force the
            # same rewrite deterministically without staging anything.
            subprocess.run(["git", "read-tree", "HEAD"], cwd=project, check=True, capture_output=True)
            subprocess.run(["git", "status", "--porcelain"], cwd=project, check=True,
                           capture_output=True)
        return {"summary": "done", "questions": []}

    def review(project, context):
        if build_in_place:
            (project / "target/Review.class").write_bytes(b"\x01")
            subprocess.run(["git", "diff", "--stat"], cwd=project, check=True, capture_output=True)
        return {"verdict": "pass", "summary": "ok", "findings": [], "questions": []}

    return LoopServices(generate_smoke=lambda project, state: smoke_payload(change_id),
                        implement=implement, review=review)


class WorkspaceScratchTests(unittest.TestCase):
    def test_only_star_is_a_name_wildcard_and_relative_paths_keep_prefix_semantics(self):
        self.assertTrue(_is_scratch("nested/package.egg-info/PKG-INFO", ["*.egg-info"]))
        self.assertTrue(_is_scratch("nested/package\nextra.egg-info/file", ["*.egg-info"]))
        self.assertFalse(_is_scratch("nested/package.egg-info/PKG-INFO", ["other*.egg-info"]))
        self.assertFalse(_is_scratch("nested/pkg1.egg-info/file", ["pkg?.egg-info"]))
        self.assertFalse(_is_scratch("nested/pkg1.egg-info/file", ["pkg[1].egg-info"]))
        self.assertTrue(_is_scratch("cache/artifacts/output.bin", ["cache/artifacts"]))
        self.assertFalse(_is_scratch("other/artifacts/output.bin", ["cache/artifacts"]))

    def test_glob_skips_root_and_nested_directories_but_not_control(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / ".storyloop").mkdir()
            (root / ".storyloop/config.json").write_text(
                json.dumps({"profile": {"scratch_paths": ["*.egg-info", "state"]}}),
                encoding="utf-8")
            before = capture_snapshot(root, include_control=True, include_ignored=True)
            for relative in ("one.egg-info/PKG-INFO", "nested/two.egg-info/PKG-INFO",
                             ".storyloop/state/marker"):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("generated", encoding="utf-8")
            after = capture_snapshot(root, include_control=True, include_ignored=True)
            changes = [item.path for item in snapshot_changes(before, after)]
            self.assertFalse(any("egg-info" in path for path in changes))
            self.assertIn(".storyloop/state/marker", changes)

    def test_glob_output_from_writer_and_reader_preserves_config(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            change_id = _approved(root, {"scratch_paths": ["*.egg-info"]})
            config = root / ".storyloop/config.json"
            original = json.loads(config.read_text(encoding="utf-8"))["profile"]["scratch_paths"]
            services = _services(change_id, build_in_place=False)

            def implement(project, context):
                result = services.implement(project, context)
                for relative in ("one.egg-info/PKG-INFO", "nested/two.egg-info/PKG-INFO"):
                    path = project / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("generated", encoding="utf-8")
                return result

            def review(project, context):
                path = project / "review_pkg/third.egg-info/PKG-INFO"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("generated", encoding="utf-8")
                return services.review(project, context)

            result = run_change(root, change_id, LoopServices(
                generate_smoke=services.generate_smoke, implement=implement, review=review))
            self.assertEqual(result["status"], "PASSED", result)
            self.assertEqual(json.loads(config.read_text(encoding="utf-8"))["profile"]["scratch_paths"],
                             original)

    def test_snapshot_skips_declared_scratch_directories_even_with_links(self):
        for declared in ([], ["node_modules"]):
            with tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                (root / "node_modules/.bin").mkdir(parents=True)
                (root / "node_modules/pkg").mkdir()
                (root / "node_modules/pkg/cli.js").write_text("", encoding="utf-8")
                os.symlink("../pkg/cli.js", root / "node_modules/.bin/cli")
                (root / ".storyloop").mkdir()
                (root / ".storyloop/config.json").write_text(
                    json.dumps({"profile": {"scratch_paths": declared}}), encoding="utf-8")
                if declared:
                    entries = dict(capture_snapshot(root, include_control=True, include_ignored=True).entries)
                    self.assertFalse(any(path.startswith("node_modules/") for path in entries))
                else:
                    with self.assertRaises(StoryLoopError):
                        capture_snapshot(root, include_control=True, include_ignored=True)

    def test_declared_scratch_paths_and_index_refresh_are_tolerated(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            change_id = _approved(root, {"scratch_paths": ["target"]})
            result = run_change(root, change_id, _services(change_id, build_in_place=True))
            self.assertEqual(result["status"], "PASSED", result)

    def test_undeclared_ignored_output_still_taints(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            change_id = _approved(root, {"scratch_paths": ["*.egg-info"]})
            with self.assertRaises(StoryLoopError) as caught:
                run_change(root, change_id, _services(change_id, build_in_place=True))
            self.assertEqual(caught.exception.code, "WORKSPACE_TAINTED")

    def test_staging_files_is_never_tolerated(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            change_id = _approved(root, {"scratch_paths": ["target"]})
            services = _services(change_id, build_in_place=False)
            original = services.implement

            def implement(project, context):
                response = original(project, context)
                subprocess.run(["git", "add", "modules"], cwd=project, check=True)
                return response

            tainted = LoopServices(generate_smoke=services.generate_smoke,
                                   implement=implement, review=services.review)
            with self.assertRaises(StoryLoopError) as caught:
                run_change(root, change_id, tainted)
            self.assertEqual(caught.exception.code, "WORKSPACE_TAINTED")


if __name__ == "__main__":
    unittest.main()
