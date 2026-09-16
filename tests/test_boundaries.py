"""Filesystem boundary contracts for stage implementation attempts."""

from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from storyloop import boundaries
from storyloop.boundaries import (
    FileFingerprint,
    WorkspaceSnapshot,
    _is_link,
    _open_descriptor,
    capture_snapshot,
    verify_stage_changes,
)
from storyloop.models import StoryLoopError


def stage() -> dict:
    return {
        "id": "feature",
        "depends_on": [],
        "specs": ["openspec/changes/feature/proposal.md"],
        "write_paths": ["service/business/**"],
        "protected_paths": ["acceptance/**", "openspec/**"],
        "judges": [],
    }


class BoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "project"
        self.write("service/business/existing.py", "before")
        self.write("service/business/remove.py", "remove")
        self.write("acceptance/check.py", "assert behavior")
        self.write("openspec/changes/feature/proposal.md", "# Feature")
        self.write("README.md", "read only")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write(self, relative: str, content: str | bytes) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
        return path

    def test_reports_allowed_add_modify_delete_in_portable_sorted_order(self):
        before = capture_snapshot(self.root)
        self.write("service/business/新增.py", "new")
        self.write("service/business/existing.py", "after")
        (self.root / "service/business/remove.py").unlink()

        changes = verify_stage_changes(before, capture_snapshot(self.root), stage())

        self.assertEqual([(item.path, item.kind) for item in changes], [
            ("service/business/existing.py", "modified"),
            ("service/business/remove.py", "deleted"),
            ("service/business/新增.py", "added"),
        ])
        self.assertTrue(all("\\" not in item.path for item in changes))

    def test_blocks_outside_write_without_deleting_user_file(self):
        before = capture_snapshot(self.root)
        escaped = self.write("service/application.py", "outside")

        with self.assertRaisesRegex(StoryLoopError, "escaped write_paths"):
            verify_stage_changes(before, capture_snapshot(self.root), stage())
        self.assertTrue(escaped.exists())

    def test_blocks_protected_file_modification(self):
        before = capture_snapshot(self.root)
        self.write("acceptance/check.py", "tampered")

        with self.assertRaisesRegex(StoryLoopError, "Protected paths changed"):
            verify_stage_changes(before, capture_snapshot(self.root), stage())

    def test_protected_boundaries_are_case_insensitive_for_portability(self):
        root = self.root.resolve()
        fingerprint = FileFingerprint(1, "0" * 64)
        before = WorkspaceSnapshot(root, ())
        after = WorkspaceSnapshot(root, (("service/secrets/secret.py", fingerprint),))
        portable = stage()
        portable["write_paths"] = ["service/**"]
        portable["protected_paths"] = ["Service/Secrets/**"]

        with self.assertRaisesRegex(StoryLoopError, "Protected paths changed"):
            verify_stage_changes(before, after, portable)

    def test_blocks_protected_file_deletion(self):
        before = capture_snapshot(self.root)
        (self.root / "openspec/changes/feature/proposal.md").unlink()

        with self.assertRaisesRegex(StoryLoopError, "Protected paths changed"):
            verify_stage_changes(before, capture_snapshot(self.root), stage())

    def test_ignores_fixed_build_cache_directories(self):
        before = capture_snapshot(self.root)
        self.write("service/__pycache__/generated.pyc", b"cache")
        self.write("target/classes/generated.class", b"cache")

        self.assertEqual(verify_stage_changes(before, capture_snapshot(self.root), stage()), [])

    def test_tracks_hidden_source_files_instead_of_silently_ignoring_them(self):
        before = capture_snapshot(self.root)
        self.write(".config", "changed")

        with self.assertRaisesRegex(StoryLoopError, "escaped write_paths"):
            verify_stage_changes(before, capture_snapshot(self.root), stage())

    def test_rejects_sensitive_files_without_exposing_their_contents(self):
        marker = "never-expose-this-value"
        self.write("service/.env", marker)

        with self.assertRaises(StoryLoopError) as caught:
            capture_snapshot(self.root)
        self.assertNotIn(marker, str(caught.exception))

    def test_rejects_symbolic_links_when_the_platform_allows_them(self):
        target = self.write("outside.txt", "target")
        link = self.root / "service/business/link.py"
        try:
            os.symlink(target, link)
        except OSError:
            link.write_text("simulated link entry", encoding="utf-8")
            with patch("storyloop.boundaries._is_link", side_effect=lambda path: path.name == "link.py"):
                with self.assertRaisesRegex(StoryLoopError, "Links are not allowed"):
                    capture_snapshot(self.root)
            return

        with self.assertRaisesRegex(StoryLoopError, "Links are not allowed"):
            capture_snapshot(self.root)

    def test_detects_windows_reparse_points_without_path_is_junction(self):
        attributes = stat.FILE_ATTRIBUTE_REPARSE_POINT
        metadata = SimpleNamespace(st_file_attributes=attributes)

        with patch.object(Path, "is_symlink", return_value=False), \
                patch("storyloop.boundaries.os.name", "nt"), \
                patch("storyloop.boundaries.os.lstat", return_value=metadata):
            self.assertTrue(_is_link(self.root))

    @unittest.skipUnless(os.name == "nt", "Windows handle semantics")
    def test_windows_open_never_falls_back_to_following_reparse_points(self):
        path = self.root / "README.md"
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
        try:
            with patch("storyloop.boundaries._open_windows_descriptor",
                       return_value=descriptor) as secure_open, \
                    patch("storyloop.boundaries.os.open",
                          side_effect=AssertionError("unsafe os.open fallback")):
                self.assertEqual(_open_descriptor(path), descriptor)
            secure_open.assert_called_once_with(path)
        finally:
            os.close(descriptor)

    def test_rejects_hard_links_that_can_escape_write_boundaries(self):
        outside = Path(self.temp.name) / "outside-source.py"
        outside.write_text("shared inode", encoding="utf-8")
        link = self.root / "service/business/hard-link.py"
        try:
            os.link(outside, link)
        except OSError as exc:
            self.skipTest(f"Hard links unavailable: {type(exc).__name__}")

        with self.assertRaisesRegex(StoryLoopError, "Hard links are not allowed"):
            capture_snapshot(self.root)

    def test_streams_large_unicode_file_and_detects_its_change(self):
        large = b"x" * (2 * 1024 * 1024)
        self.write("service/business/故事.bin", large)
        before = capture_snapshot(self.root)
        self.write("service/business/故事.bin", large + b"y")

        changes = verify_stage_changes(before, capture_snapshot(self.root), stage())
        self.assertEqual([(item.path, item.kind) for item in changes],
                         [("service/business/故事.bin", "modified")])

    def test_rejects_a_file_added_during_snapshot_capture(self):
        original = boundaries._fingerprint
        changed = False

        def add_file_after_first_fingerprint(path: Path) -> FileFingerprint:
            nonlocal changed
            result = original(path)
            if not changed:
                changed = True
                self.write("zz-concurrent.py", "late addition")
            return result

        with patch("storyloop.boundaries._fingerprint", side_effect=add_file_after_first_fingerprint):
            with self.assertRaisesRegex(StoryLoopError, "Workspace changed"):
                capture_snapshot(self.root)

    def test_rejects_a_file_removed_during_snapshot_capture(self):
        original = boundaries._fingerprint
        changed = False

        def remove_file_after_first_fingerprint(path: Path) -> FileFingerprint:
            nonlocal changed
            result = original(path)
            if path.name == "README.md" and not changed:
                changed = True
                path.unlink()
            return result

        with patch("storyloop.boundaries._fingerprint", side_effect=remove_file_after_first_fingerprint):
            with self.assertRaisesRegex(StoryLoopError, "Workspace changed"):
                capture_snapshot(self.root)

    def test_rejects_atomic_replacement_during_snapshot_capture(self):
        original = boundaries._fingerprint
        replaced = False

        def replace_after_fingerprint(path: Path) -> FileFingerprint:
            nonlocal replaced
            result = original(path)
            if path.name == "README.md" and not replaced:
                replaced = True
                replacement = Path(self.temp.name) / "replacement"
                replacement.write_text("replacement", encoding="utf-8")
                os.replace(replacement, path)
            return result

        with patch("storyloop.boundaries._fingerprint", side_effect=replace_after_fingerprint):
            with self.assertRaisesRegex(StoryLoopError, "Workspace changed"):
                capture_snapshot(self.root)

    def test_enforces_snapshot_file_limit(self):
        with self.assertRaisesRegex(StoryLoopError, "file limit"):
            capture_snapshot(self.root, max_files=2)

    def test_snapshot_entries_are_immutable(self):
        snapshot = capture_snapshot(self.root)
        with self.assertRaises(TypeError):
            snapshot.entries[0] = snapshot.entries[0]

    def test_rejects_snapshots_from_different_roots(self):
        before = capture_snapshot(self.root)
        other = Path(self.temp.name) / "other"
        other.mkdir()

        with self.assertRaisesRegex(StoryLoopError, "same project root"):
            verify_stage_changes(before, capture_snapshot(other), stage())

    def test_rejects_write_boundaries_hidden_by_ignored_directories(self):
        before = capture_snapshot(self.root)
        for boundary in ("node_modules/**", "service/node_modules/**"):
            invalid = stage()
            invalid["write_paths"] = [boundary]

            with self.assertRaisesRegex(StoryLoopError, "ignored directories"):
                verify_stage_changes(before, before, invalid)

    def test_deep_paths_do_not_exhaust_python_recursion(self):
        root = self.root.resolve()
        deep_path = "service/business/" + "/".join(["nested"] * 1500) + "/file.py"
        fingerprint = FileFingerprint(1, "0" * 64)
        before = WorkspaceSnapshot(root, ())
        after = WorkspaceSnapshot(root, ((deep_path, fingerprint),))

        try:
            changes = verify_stage_changes(before, after, stage())
        except RecursionError:
            self.fail("deep paths must not exhaust Python recursion")
        self.assertEqual([(item.path, item.kind) for item in changes], [(deep_path, "added")])

if __name__ == "__main__":
    unittest.main()



