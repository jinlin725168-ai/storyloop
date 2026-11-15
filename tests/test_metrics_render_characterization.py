"""Preserved metrics output behavior before the L1 text change."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
import unittest

from storyloop.cli import main
from storyloop.metrics_render import render_metrics
from tests.metrics_support import metrics_project, write_change, write_report, write_run


def run_metrics(*args: object) -> tuple[int, str, str]:
    output, errors = StringIO(), StringIO()
    with redirect_stdout(output), redirect_stderr(errors):
        code = main(["metrics", *(str(arg) for arg in args)])
    return code, output.getvalue(), errors.getvalue()


class MetricsRenderCharacterizationTests(unittest.TestCase):
    def test_empty_project_keeps_l1_no_sample_value(self):
        code, output, errors = run_metrics(metrics_project())

        self.assertEqual((code, errors), (0, ""))
        l1_row = next(line for line in output.splitlines() if line.startswith("| L1 |"))
        self.assertEqual(l1_row.split("|")[2].strip(), "无数据（NO_SAMPLE）")

    def test_json_keeps_l1_intervention_counts_and_types(self):
        project = metrics_project()
        write_change(project, "CHANGE-A")
        events = [
            {"kind": "contract_approval", "at": "2026-10-10T01:00:00+00:00",
             "stop": "DRAFT", "change_id": "CHANGE-A", "source": "approve"},
            {"kind": "overrule", "at": "2026-10-10T01:01:00+00:00",
             "stop": "REVIEW", "change_id": "CHANGE-A", "source": "run"},
            {"kind": "acceptance_repair", "at": "2026-10-10T01:02:00+00:00",
             "stop": "ORACLE_DEFECT", "change_id": "CHANGE-A", "source": "engine",
             "automatic": True},
        ]
        write_run(project, "CHANGE-A", intervention_events=events)
        write_report(project, "CHANGE-A")

        code, output, errors = run_metrics(project, "--json")

        self.assertEqual((code, errors), (0, ""))
        l1 = json.loads(output)["metrics"]["L1"]
        self.assertIn("CHANGE-A", l1["by_change"])
        row = l1["by_change"]["CHANGE-A"]
        for field, expected in (("routine", 1), ("nonroutine", 1),
                                ("automatic_repair", 1)):
            self.assertEqual(row[field], expected)
            self.assertEqual(l1["summary"][field], expected)
        self.assertEqual(row["routine_types"]["contract_approval"], 1)
        self.assertEqual(row["nonroutine_types"]["overrule"], 1)
        self.assertEqual(l1["summary"]["routine_types"]["contract_approval"], 1)
        self.assertEqual(l1["summary"]["nonroutine_types"]["overrule"], 1)

    def test_other_metric_row_and_l1_details_remain_visible(self):
        result = {
            "status": "METRICS", "generated_at": "2026-10-20T12:00:00+00:00",
            "changes": 1, "metrics": {
                "M1": {"id": "M1", "value": 0.25, "numerator": 3,
                       "denominator": 12, "n": 12},
                "L1": {"id": "L1", "by_change": {"CHANGE-A": {"nonroutine": 1}},
                       "summary": {"nonroutine": 1, "routine": 0,
                                   "automatic_repair": 0}},
            },
        }

        output = render_metrics(result)
        self.assertIn("| M1 | 0.25 | 3/12；n=12 |", output)
        l1_row = next(line for line in output.splitlines() if line.startswith("| L1 |"))
        details = l1_row.split("|")[4]
        self.assertIn('"by_change"', details)
        self.assertIn('"summary"', details)


if __name__ == "__main__":
    unittest.main()
