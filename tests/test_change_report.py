import json
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from storyloop.approvals import approve_change
from storyloop.baseline import bootstrap_project
from storyloop.change_report import _telemetry_view, generate_change_report
from tests.routing_support import legacy_create_change as create_change
from storyloop.models import StoryLoopError
from storyloop.smoke import install_smoke_suite
from storyloop.smoke_runner import run_smoke
from tests.support import product
from tests.test_baseline_behavior import git, repository


NOW = datetime(2026, 9, 16, tzinfo=timezone.utc)


def _layers(acceptance: dict) -> dict:
    skipped = {"status": "SKIPPED", "results": []}
    return {"unit": skipped, "integration": skipped, "acceptance": acceptance, "smoke": skipped, "checks": skipped}


class ChangeReportTests(unittest.TestCase):
    def test_report_telemetry_retains_complete_call_coverage_fields(self):
        telemetry = {"calls": 2, "known_cost_usd": 0.25, "input_tokens": None,
                     "output_tokens": 8, "turns": 3, "models": ["m"], "vendors": ["v"],
                     "tiers": ["standard"], "skills": [{"name": "s"}],
                     "coverage": {"cost_usd": {"known": 1, "total": 2}}}
        view = _telemetry_view(telemetry)
        for key, value in telemetry.items():
            self.assertEqual(view[key], value)

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
                "command": ["{python}", f"acceptance/changes/{change_id}/flow.py"],
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
                root, change_id, head, _layers(smoke),
                {"verdict": "pass", "summary": "ok", "findings": [], "questions": []},
            )
            report = json.loads(path.read_text(encoding="utf-8"))
            trace = report["traceability"][0]
            self.assertEqual(trace["story"]["id"], "ST-1")
            self.assertEqual(trace["criterion"]["id"], "AC-1")
            self.assertEqual(trace["tests"][0]["id"], "SMOKE-1")
            self.assertEqual(trace["tests"][0]["result"]["status"], "PASS")
            self.assertEqual(trace["evidence_sha256"], smoke["evidence_sha256"])
            self.assertEqual(report["implementation_commit"], head)
            self.assertEqual(set(report["presentation"]["sections"]), set("0123456"))
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
                    root, change_id, git(root, "rev-parse", "HEAD"), _layers(forged),
                    {"verdict": "pass", "summary": "ok", "findings": [], "questions": []},
                )
        finally:
            temporary.cleanup()


    def test_the_report_shows_the_oracle_self_check(self):
        temporary, root, change_id, smoke = self._setup()
        try:
            layers = _layers(smoke)
            layers["unit"] = {"status": "PASSED", "uncounted": [], "results": [
                {"id": "REGRESSION-001", "status": "PASS",
                 "counts": {"executed": 2, "failed": 0, "skipped": 0, "parser": "unittest"}}]}
            path = generate_change_report(
                root, change_id, git(root, "rev-parse", "HEAD"), layers,
                {"verdict": "pass", "summary": "ok", "findings": [], "questions": []},
                oracle={"baseline": {"unit": [2], "integration": []}},
                skills_used={"oracle-craft": "1.0.0"},
            )
            report = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(report["oracle"]["baseline"]["unit"], [2])
            self.assertEqual(report["oracle"]["executed"]["unit"][0]["executed"], 2)
            self.assertEqual(report["oracle"]["skills_used"], {"oracle-craft": "1.0.0"})
            markdown = path.with_suffix(".md").read_text(encoding="utf-8")
            self.assertIn("## 判据自证", markdown)
            self.assertIn("| unit | REGRESSION-001 | 2 | 2 | unittest |", markdown)
            self.assertIn("oracle-craft 1.0.0", markdown)
        finally:
            temporary.cleanup()


if __name__ == "__main__":
    unittest.main()
