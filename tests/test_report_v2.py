"""Report v2 (roadmap F1, issue #64): a short summary in Git, the full report on demand.

A one-line CLI change committed about 8,800 report lines (report.json 6,292 + report.md 2,338 on
sqlite-utils #481): `presentation` duplicated the raw evidence and report.md rendered all of it.
"""
import json
from pathlib import Path
import unittest

from storyloop.change_report import generate_change_report, merge_receipt_bytes
from tests.test_baseline_behavior import git
from tests.test_change_report import ChangeReportTests, _layers

REVIEW = {"verdict": "pass", "summary": "ok", "findings": [], "questions": []}


class ReportV2Tests(unittest.TestCase):
    def setUp(self):
        temporary, self.root, self.change_id, self.smoke = ChangeReportTests._setup(self)
        self.addCleanup(temporary.cleanup)
        head = git(self.root, "rev-parse", "HEAD").strip()
        self.path = generate_change_report(self.root, self.change_id, head, _layers(self.smoke), REVIEW)

    def test_json_keeps_the_evidence_without_the_rendered_copy(self):
        report = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(report["version"], 2)
        self.assertNotIn("presentation", report)
        self.assertEqual(report["traceability"][0]["criterion"]["id"], "AC-1")
        self.assertIn("acceptance", report["layers"])

    def test_markdown_is_a_short_summary_that_says_where_the_full_report_is(self):
        text = self.path.with_suffix(".md").read_text(encoding="utf-8")
        self.assertLess(len(text.splitlines()), 120)
        for part in (self.change_id, "PASSED", "AC-1", "SMOKE-1", "acceptance", "--full"):
            self.assertIn(part, text)

    def test_merge_receipt_renders_the_same_summary_from_the_report_alone(self):
        report = json.loads(self.path.read_text(encoding="utf-8"))
        json_bytes, markdown = merge_receipt_bytes(report, "a" * 40)
        self.assertEqual(json.loads(json_bytes)["merge_commit"], "a" * 40)
        self.assertIn("a" * 40, markdown.decode("utf-8"))
        self.assertEqual(merge_receipt_bytes(report, "a" * 40), (json_bytes, markdown))

    def test_report_full_renders_every_section_outside_git(self):
        from storyloop.cli import dispatch, parser
        before = git(self.root, "status", "--porcelain")
        result = dispatch(parser().parse_args(["report", str(self.root), "--change", self.change_id, "--full"]))
        full = Path(result["report"])
        self.assertFalse(full.is_relative_to(self.root / "reports"))
        text = full.read_text(encoding="utf-8")
        for heading in ("## 0. 头部", "## 1. 结论", "## 3. 证据", "## 5. 成本"):
            self.assertIn(heading, text)
        self.assertEqual(git(self.root, "status", "--porcelain"), before)  # nothing new to commit


if __name__ == "__main__":
    unittest.main()
