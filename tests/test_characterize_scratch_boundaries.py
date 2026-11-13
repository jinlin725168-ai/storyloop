"""Existing snapshot exclusions that the scratch matcher must preserve."""

import json
from pathlib import Path
import tempfile
import unittest

from storyloop.boundaries import capture_snapshot, snapshot_changes


class ScratchSnapshotCharacterization(unittest.TestCase):
    def test_exact_name_skips_root_and_nested_directories(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / ".storyloop").mkdir()
            (root / ".storyloop/config.json").write_text(
                json.dumps({"profile": {"scratch_paths": ["target"]}}), encoding="utf-8")
            (root / "service").mkdir()
            before = capture_snapshot(root, include_control=True, include_ignored=True)
            for relative in ("target/root.bin", "service/target/nested.bin"):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"build output")
            after = capture_snapshot(root, include_control=True, include_ignored=True)
            self.assertEqual(snapshot_changes(before, after), [])
            self.assertIn(".storyloop/config.json", dict(after.entries))

    def test_relative_path_exclusion_keeps_other_same_named_directories_visible(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / ".storyloop").mkdir()
            (root / ".storyloop/config.json").write_text(
                json.dumps({"profile": {"scratch_paths": ["cache/artifacts"]}}),
                encoding="utf-8")
            excluded = root / "cache/artifacts/output.bin"
            visible = root / "other/artifacts/output.bin"
            for path in (excluded, visible):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"output")
            entries = dict(capture_snapshot(root, include_control=True, include_ignored=True).entries)
            self.assertIn("other/artifacts/output.bin", entries)
            self.assertNotIn("cache/artifacts/output.bin", entries)

    def test_control_directory_remains_visible_when_declared_as_scratch(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / ".storyloop").mkdir()
            config = root / ".storyloop/config.json"
            config.write_text(json.dumps({"profile": {"scratch_paths": [".storyloop"]}}),
                              encoding="utf-8")
            before = capture_snapshot(root, include_control=True, include_ignored=True)
            config.write_text(json.dumps({"profile": {"scratch_paths": [".storyloop"],
                                                      "marker": "changed"}}), encoding="utf-8")
            changes = snapshot_changes(before, capture_snapshot(
                root, include_control=True, include_ignored=True))
            self.assertIn(".storyloop/config.json", [item.path for item in changes])


if __name__ == "__main__":
    unittest.main()
