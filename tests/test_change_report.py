import json
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from storyloop.approvals import approve_change
from storyloop.baseline import bootstrap_project
from storyloop.change_report import generate_change_report
from storyloop.changes import create_change
from storyloop.models import StoryLoopError
from storyloop.smoke import install_smoke_suite
from storyloop.smoke_runner import run_smoke
from tests.support import product
from tests.test_baseline_behavior import git, repository


NOW = datetime(2026, 9, 16, tzinfo=timezone.utc)


class ChangeReportTests(unittest.TestCase):
    def _setup(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        repository(root)
        bootstrap_project(root)
        state = create_change(root, "idea", product(), now=lambda: NOW)
        change_id = state["change_id"]
        approve_change(root, change_id, now=lambda: NOW)
        install_smoke_suite(root, change_id, [{"path": "flow.py", "content": "raise SystemExit(0)\n"}], {
            "version": 1, "tests": [{
                "id": "SMOKE-1", "criteria": ["AC-1"],
                "command": ["{python}", f"acceptance/smoke/{change_id}/flow.py"],
                "timeout_seconds": 10,
            }],
        })
        git(root, "add", "acceptance")
        git(root, "commit", "-q", "-m", "candidate")
        return temporary, root, change_id, run_smoke(root, change_id)

    def test_report_maps_story_criterion_test_result_evidence_and_commit(self):
        temporary, root, change_id, smoke = self._setup()
        try:
            head = git(root, "rev-parse", "HEAD")
            path = generate_change_report(
                root, change_id, head, {"status": "SKIPPED", "results": []},
                smoke, {"verdict": "pass", "summary": "ok", "findings": [], "questions": []},
            )
            report = json.loads(path.read_text(encoding="utf-8"))
            trace = report["traceability"][0]
            self.assertEqual(trace["story"]["id"], "ST-1")
            self.assertEqual(trace["criterion"]["id"], "AC-1")
            self.assertEqual(trace["tests"][0]["id"], "SMOKE-1")
            self.assertEqual(trace["tests"][0]["result"]["status"], "PASS")
            self.assertEqual(trace["evidence_sha256"], smoke["evidence_sha256"])
            self.assertEqual(report["implementation_commit"], head)
            self.assertTrue(path.with_suffix(".md").is_file())
        finally:
            temporary.cleanup()

    def test_failed_or_unmapped_acceptance_cannot_produce_pass_report(self):
        temporary, root, change_id, smoke = self._setup()
        try:
            forged = dict(smoke)
            forged["status"] = "FAILED"
            with self.assertRaisesRegex(StoryLoopError, "pass|evidence|acceptance"):
                generate_change_report(
                    root, change_id, git(root, "rev-parse", "HEAD"),
                    {"status": "SKIPPED", "results": []}, forged,
                    {"verdict": "pass", "summary": "ok", "findings": [], "questions": []},
                )
        finally:
            temporary.cleanup()


if __name__ == "__main__":
    unittest.main()
