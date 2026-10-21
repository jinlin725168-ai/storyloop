"""Focused regressions for the final outer-loop review."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from storyloop.metrics_compute import first_pass, interventions, lessons, rework
from storyloop.metrics_render import render_metrics
from storyloop.metrics_rules import DEFAULTS, suggest
from tests.test_metrics_compute import rec


class MetricsReviewTests(unittest.TestCase):
    def test_m10_renders_counts_and_only_top_three_automation_directions(self):
        value = interventions([rec("A", answers=5, repairs=4, resets=3,
                                    manual_acceptance=2)])

        text = render_metrics({"metrics": {"M10": value}})

        row = next(line for line in text.splitlines() if line.startswith("| M10 |"))
        self.assertIn("answers=5", row)
        self.assertIn("repairs=4", row)
        self.assertIn("resets=3", row)
        self.assertIn("manual_acceptance=2", row)
        self.assertNotIn("无数据", row)
        section = text.split("## 自动化方向参考\n", 1)[1].split("\n## ", 1)[0]
        self.assertIn("answers=5", section)
        self.assertIn("repairs=4", section)
        self.assertIn("resets=3", section)
        self.assertNotIn("manual_acceptance", section)

    def test_m10_without_interventions_renders_zero_and_no_directions(self):
        text = render_metrics({"metrics": {"M10": interventions([rec("A")])}})

        self.assertIn("| M10 | 0 | 0/1；n=1 |", text)
        section = text.split("## 自动化方向参考\n", 1)[1].split("\n## ", 1)[0]
        self.assertIn("无", section)
        self.assertNotIn("answers", section)

    def test_r2_uses_real_implementation_failure_ids_and_caps_evidence(self):
        for count in (10, 12):
            with self.subTest(count=count):
                records = [rec(f"I-{number}", failures=[{"attribution": "implement"}] * 2)
                           for number in range(count)]
                if count > 10:
                    records.append(rec("CONTRACT", failures=[{"attribution": "contract"}]))
                metrics = {"M1": first_pass(records), "M3": rework(records)}

                result = next(item for item in suggest(metrics, DEFAULTS)
                              if item["rule"] == "R2")

                self.assertEqual(result["evidence"], [f"I-{number}" for number in range(10)])
                self.assertEqual(result["more"], count - 10)
                self.assertNotIn("CONTRACT", result["evidence"])

    def test_lesson_recurrence_is_unknown_when_all_provided_evidence_is_missing(self):
        for state in (None, {}, {"L-1": {"recurrences": ["A"]}}):
            with self.subTest(state=state), tempfile.TemporaryDirectory() as directory:
                project = self.lesson_project(directory, state)

                result = lessons(project, [rec("A", ledger=None)])["recurrence_rate"]

                self.assertIsNone(result["value"])
                self.assertEqual(result["reason"], "UNREADABLE")
                self.assertEqual((result["numerator"], result["denominator"]), (0, 0))
                self.assertEqual(result["missing"], [{"change_id": "A", "reason": "UNREADABLE"}])

    def test_lesson_recurrence_keeps_known_counts_but_partial_evidence_is_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            project = self.lesson_project(directory, {"L-1": {"recurrences": ["B", "OUT"]}})
            records = [rec("A", ledger=[{"lessons_provided": ["L-1"],
                                         "lessons_claimed": ["L-1"]}]),
                       rec("B", ledger=[]), rec("UNKNOWN", ledger=None)]

            result = lessons(project, records)["recurrence_rate"]

        self.assertIsNone(result["value"])
        self.assertEqual(result["reason"], "UNREADABLE")
        self.assertEqual((result["numerator"], result["denominator"], result["n"]), (1, 1, 1))
        self.assertEqual(result["changes"], ["B"])
        self.assertEqual(result["missing"], [{"change_id": "UNKNOWN", "reason": "UNREADABLE"}])
        output = suggest({"M12": {"recurrence_rate": result}}, DEFAULTS)
        self.assertTrue(any(item["rule"] == "R12" and "unavailable" in item for item in output))

    def test_lesson_recurrence_without_any_provided_lesson_has_no_sample(self):
        with tempfile.TemporaryDirectory() as directory:
            project = self.lesson_project(directory, {})

            result = lessons(project, [rec("A", ledger=[])])["recurrence_rate"]

        self.assertEqual((result["value"], result["n"], result["reason"]), (None, 0, "NO_SAMPLE"))

    def test_symlink_capability_failure_skips_only_the_symlink_cases(self):
        from tests.test_ci_results import CiResultTests
        from tests.test_metrics_command import MetricsCommandTests

        cases = [
            CiResultTests("test_ci_results_reject_symlinks_oversize_and_invalid_timestamps"),
            MetricsCommandTests("test_save_refuses_symlink_parent_without_writing_outside"),
        ]
        for case in cases:
            with self.subTest(case=case.id()):
                result = unittest.TestResult()
                with patch.object(Path, "symlink_to", side_effect=PermissionError("not permitted")):
                    case.run(result)
                self.assertEqual(result.errors, [])
                self.assertEqual(result.failures, [])
                self.assertEqual(len(result.skipped), 1)

    @staticmethod
    def lesson_project(directory, state):
        project = Path(directory)
        (project / "openspec/lessons").mkdir(parents=True)
        if state is None:
            return project
        path = project / ".storyloop/state/lessons.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(state), encoding="utf-8")
        return project
