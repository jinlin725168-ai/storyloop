"""Existing post-call scratch filtering and protected control state."""

import json
from pathlib import Path
import tempfile
import unittest

from storyloop.boundaries import capture_snapshot
from storyloop.models import StoryLoopError
from storyloop.project_loop_guards import _is_scratch, verify_read_only_snapshot


class ScratchGuardCharacterization(unittest.TestCase):
    def test_exact_name_and_relative_prefix_filter_read_only_output(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / ".storyloop").mkdir()
            config = root / ".storyloop/config.json"
            config.write_text(json.dumps({"profile": {"scratch_paths": [
                "target", "cache/artifacts"]}}), encoding="utf-8")
            original_config = config.read_bytes()
            (root / "service").mkdir()
            (root / "cache").mkdir()
            before = capture_snapshot(root, include_control=True, include_ignored=True)
            for relative in ("target/root.bin", "service/target/nested.bin",
                             "cache/artifacts/output.bin"):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"output")
            after = capture_snapshot(root, include_control=True, include_ignored=True)
            self.assertIsNone(verify_read_only_snapshot(
                before, after, None, ["target", "cache/artifacts"]))
            self.assertEqual(config.read_bytes(), original_config)
            self.assertTrue(_is_scratch("service/target/nested.bin", ["target"]))
            self.assertTrue(_is_scratch("cache/artifacts/output.bin", ["cache/artifacts"]))
            self.assertFalse(_is_scratch("other/artifacts/output.bin", ["cache/artifacts"]))

    def test_undeclared_output_and_control_changes_remain_tainted(self):
        for relative in ("other/ignored.bin", ".storyloop/state/injected.json"):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                (root / ".storyloop").mkdir()
                (root / ".storyloop/config.json").write_text(
                    json.dumps({"profile": {"scratch_paths": ["target", "injected.json"]}}),
                    encoding="utf-8")
                before = capture_snapshot(root, include_control=True, include_ignored=True)
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"output")
                after = capture_snapshot(root, include_control=True, include_ignored=True)
                with self.assertRaises(StoryLoopError) as caught:
                    verify_read_only_snapshot(before, after, None, ["target", "injected.json"])
                self.assertEqual(caught.exception.code, "WORKSPACE_TAINTED")


if __name__ == "__main__":
    unittest.main()
