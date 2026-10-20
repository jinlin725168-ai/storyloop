"""Behavioral tests for bounded, read-only metrics source normalization."""

from datetime import date
import os
import shutil
import unittest

from storyloop.metrics_collect import collect
from tests.metrics_support import (
    link_report_outside_project,
    metrics_project,
    write_answers,
    write_change,
    write_ledger,
    write_manual_acceptance,
    write_report,
    write_run,
)


class CollectTests(unittest.TestCase):
    def project(self):
        project = metrics_project()
        self.addCleanup(shutil.rmtree, project, True)
        return project

    def test_report_wins_and_conflicts_are_listed(self):
        project = self.project()
        write_report(project, "FEAT-1", status="PASSED", attempts=2)
        write_run(project, "FEAT-1", status="PASSED", attempt=3)

        result = collect(project)

        self.assertEqual(result["changes"][0]["attempts"], 2)
        self.assertEqual(result["discrepancies"], [
            {"change_id": "FEAT-1", "field": "attempts", "report": 2, "run": 3},
        ])

    def test_stopped_change_without_report_is_collected_from_run_state(self):
        project = self.project()
        write_run(project, "FEAT-2", status="BUDGET_EXHAUSTED", attempt=5,
                  failures=[{"attribution": "implement"}])

        record = collect(project)["changes"][0]

        self.assertFalse(record["has_report"])
        self.assertIn("BUDGET_EXHAUSTED", record["stops"])

    def test_reports_without_run_state_still_compute(self):
        project = self.project()
        write_report(project, "FEAT-3", status="PASSED")

        record = collect(project)["changes"][0]

        self.assertIsNone(record["agent_failures"])
        self.assertEqual(record["gaps"]["agent_failures"], "UNREADABLE")

    def test_since_filters_by_approval_date(self):
        project = self.project()
        write_change(project, "FEAT-4", approved_at="2026-10-01T00:00:00+00:00")
        write_change(project, "FEAT-5", approved_at="2026-10-15T00:00:00+00:00")

        ids = [record["change_id"] for record in collect(
            project, since=date(2026, 10, 10))["changes"]]

        self.assertEqual(ids, ["FEAT-5"])

    def test_unknown_report_version_falls_back_to_run(self):
        project = self.project()
        write_report(project, "FEAT-6", version=99)
        write_run(project, "FEAT-6", status="PASSED")

        result = collect(project)

        self.assertIn({"change_id": "FEAT-6", "reason": "UNKNOWN_REPORT_VERSION"},
                      result["missing"])
        self.assertEqual(result["changes"][0]["status"], "PASSED")
        self.assertFalse(result["changes"][0]["has_report"])

    def test_contract_attribution_implies_needs_spec_fix(self):
        project = self.project()
        write_run(project, "FEAT-7", status="SUPERSEDED",
                  failures=[{"attribution": "contract"}])

        self.assertIn("NEEDS_SPEC_FIX", collect(project)["changes"][0]["stops"])

    @unittest.skipIf(os.name == "nt", "symbolic link setup is platform dependent")
    def test_linked_report_is_not_followed(self):
        project = self.project()
        outside = link_report_outside_project(project, "FEAT-8")
        self.addCleanup(shutil.rmtree, outside, True)

        result = collect(project)

        self.assertIn({"change_id": "FEAT-8", "reason": "UNREADABLE"}, result["missing"])

    @unittest.skipIf(os.name == "nt", "symbolic link setup is platform dependent")
    def test_linked_report_directory_is_reported_but_not_followed(self):
        project = self.project()
        outside = metrics_project()
        self.addCleanup(shutil.rmtree, outside, True)
        write_report(outside, "FEAT-DIR")
        reports = project / "reports/storyloop"
        reports.mkdir(parents=True)
        (reports / "FEAT-DIR").symlink_to(outside / "reports/storyloop/FEAT-DIR",
                                          target_is_directory=True)

        result = collect(project)

        self.assertIn({"change_id": "FEAT-DIR", "reason": "UNREADABLE"}, result["missing"])

    def test_main_schema_manual_decisions_and_stop_status_are_used(self):
        project = self.project()
        write_report(project, "FEAT-9", routing={
            "initial": "standard", "effective": "heavy", "history": [{"to": "heavy"}],
            "manual_decisions": {"design": {"actor": "user"},
                                 "continue_heavy": {"actor": "user"}},
        })
        write_run(project, "FEAT-9", status="NO_PROGRESS", planning={
            "plan": {"approval": None}, "continue_heavy": None,
        }, routing={"initial_tier": "standard", "effective_tier": "standard", "history": []},
                  stop={"status": "NO_PROGRESS"})

        record = collect(project)["changes"][0]

        self.assertEqual(record["tier_effective"], "heavy")
        self.assertTrue(record["design_approved"])
        self.assertTrue(record["continue_heavy"])
        self.assertIn("NO_PROGRESS", record["stops"])

    def test_answers_and_manual_acceptance_use_their_authoritative_files(self):
        project = self.project()
        write_run(project, "FEAT-10", answers=[{"stale": True}], manual_acceptance=[])
        write_answers(project, "FEAT-10", [{"answer": "one"}, {"answer": "two"}])
        write_manual_acceptance(project, "FEAT-10", [{"criterion_id": "AC-1"}])

        record = collect(project)["changes"][0]

        self.assertEqual(record["answers"], 2)
        self.assertEqual(record["manual_acceptance"], 1)

    def test_explicit_empty_lists_are_distinct_from_missing_sources(self):
        project = self.project()
        write_run(project, "EMPTY", failures=[], agent_failures=[], resets=[], repairs=[])
        write_answers(project, "EMPTY", [])
        write_manual_acceptance(project, "EMPTY", [])
        write_run(project, "MISSING")

        records = {record["change_id"]: record for record in collect(project)["changes"]}

        self.assertEqual(records["EMPTY"]["agent_failures"], [])
        self.assertEqual(records["EMPTY"]["answers"], 0)
        self.assertEqual(records["EMPTY"]["manual_acceptance"], 0)
        self.assertNotIn("answers", records["EMPTY"]["gaps"])
        self.assertIsNone(records["MISSING"]["answers"])
        self.assertEqual(records["MISSING"]["gaps"]["answers"], "UNREADABLE")

    def test_report_only_approval_date_and_change_nature_are_preserved(self):
        project = self.project()
        write_report(project, "REPORT-ONLY", approved_at="2026-10-18T00:00:00+00:00")
        write_change(project, "FIX-1", nature="fix", origin_change="FEAT-OLD")

        records = {record["change_id"]: record for record in collect(project)["changes"]}

        self.assertEqual(records["REPORT-ONLY"]["approved_at"], "2026-10-18T00:00:00+00:00")
        self.assertEqual(records["FIX-1"]["nature"], "fix")
        self.assertEqual(records["FIX-1"]["origin_change"], "FEAT-OLD")

    def test_commit_sources_and_bounded_ledger_are_normalized(self):
        project = self.project()
        write_report(project, "FEAT-11", implementation_commit="report-impl",
                     merge_commit="report-merge")
        write_run(project, "FEAT-11", source_head="source", contract_commit="contract",
                  implementation_commit="run-impl", merge_commit="run-merge")
        write_ledger(project, "FEAT-11", [{"role": "implement", "wall_ms": 12}])

        result = collect(project)
        record = result["changes"][0]

        self.assertEqual(record["commits"], {
            "source_head": "source", "contract_commit": "contract",
            "implementation_commit": "report-impl", "merge_commit": "report-merge",
        })
        self.assertEqual(record["ledger"], [{"role": "implement", "wall_ms": 12}])
        fields = {item["field"] for item in result["discrepancies"]}
        self.assertEqual(fields, {"implementation_commit", "merge_commit"})

    def test_only_persisted_signal_evidence_is_carried_forward(self):
        project = self.project()
        stories = [{"id": "US-1", "acceptance": [{"id": "AC-1"}]}]
        layers = {"acceptance": {"status": "PASSED", "results": [{"id": "T-1"}]}}
        traceability = [{"criterion": {"id": "AC-1"}, "tests": [{"id": "T-1"}]}]
        write_change(project, "FEAT-SIGNAL", stories=stories)
        write_report(project, "FEAT-SIGNAL", layers=layers, traceability=traceability,
                     review={"findings": [{"target": "oracle"}, {"target": "style"}]})

        record = collect(project)["changes"][0]

        self.assertEqual(record["signal_evidence"], {
            "stories": stories, "layers": layers, "traceability": traceability,
        })
        self.assertEqual(record["review_stop_findings"], 1)

    @unittest.skipIf(os.name == "nt", "symbolic link setup is platform dependent")
    def test_linked_ledger_is_not_followed(self):
        project = self.project()
        write_run(project, "FEAT-12")
        outside = metrics_project()
        self.addCleanup(shutil.rmtree, outside, True)
        write_ledger(outside, "FEAT-12", [{"role": "review"}])
        link = project / ".storyloop/state/telemetry/FEAT-12.json"
        link.parent.mkdir(parents=True)
        link.symlink_to(outside / ".storyloop/state/telemetry/FEAT-12.json")

        record = collect(project)["changes"][0]

        self.assertIsNone(record["ledger"])
        self.assertEqual(record["gaps"]["ledger"], "UNREADABLE")
        self.assertIn({"change_id": "FEAT-12", "reason": "UNREADABLE"},
                      collect(project)["missing"])


if __name__ == "__main__":
    unittest.main()
