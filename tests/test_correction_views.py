"""Attribution, overrules and revisions are visible in log, report and decisions.md (2a §6.2, §8)."""

import unittest

from storyloop.change_log import render_log
from storyloop.openspec_docs import render_decisions


class LogViewTests(unittest.TestCase):
    def test_log_shows_attribution_agent_failures_and_the_stop(self):
        result = {"change_id": "FEAT-1", "change_status": "APPROVED", "run_status": "NO_PROGRESS", "attempt": 3,
                  "calls": [], "error": None, "telemetry": {}, "next": "storyloop run",
                  "failures": [{"attempt": 1, "gate": "layers", "reason": "unit=FAILED",
                                "attribution": "environment", "signature": "0123abcd"}],
                  "agent_failures": [{"attempt": 2, "role": "implement", "code": "AGENT_UNAVAILABLE",
                                      "message": "timeout"}],
                  "stop": {"blocker": "根因调查后失败特征仍不变", "evidence": [], "questions": [],
                           "options": [{"action": "修正判据", "command": "storyloop acceptance-repair x"}],
                           "resume": "storyloop run x"}}
        text = render_log(result)
        self.assertIn("[environment 0123abcd]", text)
        self.assertIn("代理未回答", text)
        self.assertIn("AGENT_UNAVAILABLE", text)
        self.assertIn("阻塞点：根因调查后失败特征仍不变", text)


class DecisionsTests(unittest.TestCase):
    def test_overrules_and_the_superseded_change_are_recorded(self):
        change = {"change_id": "FEAT-2", "idea": "x", "history": [], "product": {"clarifications": []},
                  "supersedes": {"change_id": "FEAT-1", "message": "AC-1 改为…", "reason": None,
                                 "blocker": "AC-1 contradicts kept behaviour"}}
        text = render_decisions(change, [], [], [], [], overrules=[
            {"at": "2026-10-14T00:00:00+00:00", "status": "ORACLE_DEFECT", "attempt": 1,
             "reason": "判据没错", "findings": ["AC-1|acceptance/x.py"]}])
        self.assertIn("## Revision of FEAT-1", text)
        self.assertIn("AC-1 contradicts kept behaviour", text)
        self.assertIn("## Overruled attributions", text)
        self.assertIn("判据没错", text)


class ReportSectionTests(unittest.TestCase):
    def test_the_report_lists_attribution_overrules_and_revision(self):
        from storyloop.change_report import _correction_markdown, correction_view
        state = {"failures": [{"attempt": 1, "attribution": "oracle", "signature": "abc12345def",
                               "review": {"findings": [{"message": "reads .git"}]}}],
                 "overrules": [{"attempt": 1, "reason": "判据没错"}]}
        view = correction_view(state, {"supersedes": {"change_id": "FEAT-1"}})
        text = "\n".join(_correction_markdown(view))
        self.assertIn("## 失败归属", text)
        self.assertIn("| 1 | oracle | abc12345 |", text)
        self.assertIn("判据没错", text)
        self.assertIn("修订自 FEAT-1", text)

    def test_a_clean_run_has_no_section(self):
        from storyloop.change_report import _correction_markdown, correction_view
        self.assertEqual(_correction_markdown(correction_view({"failures": []}, {})), [])


if __name__ == "__main__":
    unittest.main()
