"""Public bench report output for stored Run records."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class BenchReportTests(unittest.TestCase):
    def report(self, record, as_json):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / ".storyloop/bench/runs" / f"{record['run_id']}.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(record), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "-m", "storyloop", "bench",
                 *(["--json"] if as_json else []), "report", record["run_id"],
                 "--output-root", str(root)],
                capture_output=True, text=True, encoding="utf-8", check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout) if as_json else result.stdout.splitlines()

    def test_json_summary_preserves_case_and_attempt_order(self):
        record = {"run_id": "r123", "case_revision": "2.1", "case_version": 2,
                  "scope": "base-only", "suite_source": "generated", "stage_gate": {"passed": True},
                  "cases": [
                      {"case_id": "alpha", "attempts": [
                          {"verdict": "PASSED", "gold_passed": True,
                           "termination_reason": "", "nonroutine_count": 0},
                          {"verdict": "FAILED", "gold_passed": False,
                           "termination_reason": "TIMEOUT", "nonroutine_count": 2}]},
                      {"case_id": "beta", "attempts": [
                          {"verdict": "NEEDS_HUMAN", "gold_passed": None,
                           "termination_reason": "HUMAN", "nonroutine_count": 1},
                          {"verdict": "PASSED", "gold_passed": True,
                           "termination_reason": None, "nonroutine_count": 0}]},
                  ]}
        result = self.report(record, True)
        self.assertEqual(result["summary"], {
            "attempts": 4, "passed": 2, "gold_failed": 1,
            "verdicts": {"PASSED": 2, "FAILED": 1, "NEEDS_HUMAN": 1},
            "stop_reasons": {"none": 2, "TIMEOUT": 1, "HUMAN": 1},
            "nonroutine_total": 3,
            "cases": [{"case_id": "alpha", "verdicts": ["PASSED", "FAILED"],
                       "nonroutine": [0, 2]},
                      {"case_id": "beta", "verdicts": ["NEEDS_HUMAN", "PASSED"],
                       "nonroutine": [1, 0]}],
            "stage_gate_passed": True,
        })
        self.assertEqual({key: value for key, value in result.items() if key != "summary"}, record)

    def test_text_report_sorts_stops_and_renders_gate(self):
        record = {"run_id": "r123", "case_revision": "2.1", "case_version": 2,
                  "scope": "base-only", "suite_source": "generated", "stage_gate": {"passed": False},
                  "cases": [
                      {"case_id": "alpha", "attempts": [
                          {"verdict": "PASSED", "gold_passed": True,
                           "termination_reason": None, "nonroutine_count": 0},
                          {"verdict": "FAILED", "gold_passed": False,
                           "termination_reason": "TIMEOUT", "nonroutine_count": 2}]},
                      {"case_id": "beta", "attempts": [
                          {"verdict": "NEEDS_HUMAN", "gold_passed": None,
                           "termination_reason": "HUMAN", "nonroutine_count": 1},
                          {"verdict": "PASSED", "gold_passed": True,
                           "termination_reason": "", "nonroutine_count": 0}]},
                  ]}
        self.assertEqual(self.report(record, False), [
            "Run r123（2.1，base-only，generated）",
            "通过 2/4，Gold 失败 1，非例行介入合计 3",
            "alpha：PASSED/FAILED（非例行 0/2）",
            "beta：NEEDS_HUMAN/PASSED（非例行 1/0）",
            "停点：none×2，HUMAN×1，TIMEOUT×1",
            "门槛：未通过",
        ])

    def test_empty_cases_use_version_fallback_and_omit_gate(self):
        record = {"run_id": "empty", "case_revision": None, "case_version": 2,
                  "scope": "base-only", "suite_source": "generated", "cases": []}
        self.assertEqual(self.report(record, True)["summary"], {
            "attempts": 0, "passed": 0, "gold_failed": 0, "verdicts": {},
            "stop_reasons": {}, "nonroutine_total": 0, "cases": [],
            "stage_gate_passed": None,
        })
        self.assertEqual(self.report(record, False), [
            "Run empty（2，base-only，generated）",
            "通过 0/0，Gold 失败 0，非例行介入合计 0",
            "停点：",
        ])


if __name__ == "__main__":
    unittest.main()
