"""Existing human decision flow around a linked base change."""

from pathlib import Path
import tempfile
import unittest

from storyloop.run_hints import next_step
from tests.test_baseline_behavior import git, repository


class BaseHintFlowCharacterization(unittest.TestCase):
    def test_linked_base_hint_preserves_human_steps_and_does_not_write(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            repository(project)
            marker = project / "unchanged.txt"
            marker.write_text("human work\n", encoding="utf-8")
            details = {
                "blocked_by": "BASE-1",
                "legacy_paths": ["modules/orders/service.py"],
                "source_branch": "main",
                "feature_branch": "feature/one",
            }
            before = marker.read_bytes()
            status_before = git(project, "status", "--porcelain=v1", "--untracked-files=all")
            stash_before = git(project, "stash", "list")
            hint = next_step("BASE_CHANGE_REQUIRED", "FEAT-1", project, details)

            steps = (
                "保存遗留业务改动",
                "检查并另行保存其他改动",
                "storyloop approve",
                "storyloop run",
                "自动合并并重证",
                "取回或丢弃暂存",
            )
            positions = [hint.index(step) for step in steps]
            self.assertEqual(positions, sorted(positions))
            self.assertIn("--change BASE-1", hint)
            self.assertIn("--implementer human", hint)
            self.assertEqual(marker.read_bytes(), before)
            self.assertEqual(git(project, "status", "--porcelain=v1", "--untracked-files=all"),
                             status_before)
            self.assertEqual(git(project, "stash", "list"), stash_before)


if __name__ == "__main__":
    unittest.main()
