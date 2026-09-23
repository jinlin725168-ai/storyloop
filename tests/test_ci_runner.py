from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from storyloop.approvals import approve_change
from storyloop.baseline import bootstrap_project
from storyloop.changes import create_change
from storyloop.models import StoryLoopError
from storyloop.smoke import install_smoke_suite
from storyloop.smoke_runner import run_smoke
from tests.support import product
from tests.test_baseline_behavior import repository


NOW = datetime(2026, 9, 16, tzinfo=timezone.utc)


class CiRunnerTests(unittest.TestCase):
    def _project(self, exit_code: int) -> tuple[tempfile.TemporaryDirectory, Path, str]:
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        repository(root)
        bootstrap_project(root)
        state = create_change(root, "idea", product(), now=lambda: NOW)
        change_id = state["change_id"]
        approve_change(root, change_id, now=lambda: NOW)
        path = f"acceptance/changes/{change_id}/smoke.py"
        install_smoke_suite(root, change_id, [{"path": "smoke.py", "content": f"raise SystemExit({exit_code})\n"}], {
            "version": 1,
            "tests": [{"id": "SMOKE-1", "criteria": ["AC-1"],
                       "command": ["{python}", path], "timeout_seconds": 10}],
        })
        return temporary, root, change_id

    def test_runs_manifest_as_independent_ci_entry_without_changing_ci(self):
        temporary, root, change_id = self._project(0)
        try:
            before = (root / ".github/workflows/ci.yml").read_bytes()
            result = run_smoke(root, change_id)
            self.assertEqual(result["status"], "PASSED")
            self.assertEqual(result["results"][0]["returncode"], 0)
            self.assertEqual((root / ".github/workflows/ci.yml").read_bytes(), before)
            self.assertEqual(len(result["evidence_sha256"]), 64)
        finally:
            temporary.cleanup()

    def test_red_requires_a_real_behavioral_failure_and_tampering_blocks(self):
        temporary, root, change_id = self._project(1)
        try:
            result = run_smoke(root, change_id, expect_red=True)
            self.assertEqual(result["status"], "RED")
            suite = root / "acceptance/changes" / change_id / "smoke.py"
            suite.write_text("raise SystemExit(0)\n", encoding="utf-8")
            with self.assertRaisesRegex(StoryLoopError, "modified"):
                run_smoke(root, change_id)
        finally:
            temporary.cleanup()


if __name__ == "__main__":
    unittest.main()
