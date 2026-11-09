"""Design revision findings are safe and consistent across stop views."""

from copy import deepcopy
import contextlib
from io import StringIO
import unittest

from storyloop.cli_output import display
from storyloop.change_log import render_log
from storyloop.stop_report import build_stop, render_stop, stop_of


def state_with(findings):
    return {"change_id": "FEAT-1", "status": "DESIGN_REVISION_REQUIRED",
            "attempt": 1, "failures": [], "findings": findings}


class DesignRevisionFindingsTests(unittest.TestCase):
    def test_build_shows_only_blocking_findings_and_preserves_source(self):
        findings = [
            {"target": "design", "blocking": True, "message": "修正边界 A",
             "location": "plan:constraints/0"},
            {"target": "design", "blocking": False, "message": "可选建议",
             "location": "plan:summary"},
            {"target": "oracle", "blocking": True, "message": "补充回归 B",
             "location": "plan:regression/0"},
        ]
        state = state_with(findings)
        original = deepcopy(state)
        stop = build_stop("/p", state, state["status"], {})
        self.assertEqual(state, original)
        self.assertIn("review_findings", stop)
        items = stop["review_findings"]
        self.assertEqual(len(items), 2)
        self.assertEqual([item["target"] for item in items], ["design", "oracle"])
        self.assertEqual([item["severity"] for item in items], ["阻塞", "阻塞"])
        self.assertEqual([item["message"] for item in items], ["修正边界 A", "补充回归 B"])
        self.assertEqual([item["location"] for item in items],
                         ["plan:constraints/0", "plan:regression/0"])
        rendered = "\n".join(render_stop(stop))
        for item in items:
            self.assertIn(item["summary"], rendered)
        self.assertNotIn("可选建议", rendered)

    def test_old_saved_stop_is_backfilled_without_writing_run(self):
        findings = [{"target": "design", "blocking": True, "message": "补足路径",
                     "location": "plan:regression/0"}]
        state = state_with(findings)
        state["stop"] = build_stop("/p", state, state["status"], {})
        state["stop"].pop("review_findings", None)
        original = deepcopy(state)
        shown = stop_of("/p", state)
        self.assertEqual(state, original)
        self.assertIsNot(shown, state["stop"])
        self.assertEqual(shown["review_findings"][0]["message"], "补足路径")
        self.assertEqual(shown["resume"], original["stop"]["resume"])

    def test_redacted_bounded_summary_reaches_run_status_and_log_text(self):
        findings = [
            {"target": "design", "blocking": True,
             "message": "token=fixture-secret 修正 " + "长" * 450,
             "location": "plan:constraints/0"},
            {"target": "design", "blocking": True,
             "message": "检查 https://fixture-user:fixture-pass@example.test/path",
             "location": "plan:" + "location/" * 80},
        ]
        state = state_with(findings)
        stop = build_stop("/p", state, state["status"], {})
        self.assertIn("review_findings", stop)
        items = stop["review_findings"]
        self.assertEqual(len(items), 2)
        for item in items:
            self.assertLessEqual(len(item["summary"]), 300)
            for key in ("target", "severity", "message", "location"):
                self.assertIn(item[key], item["summary"])
        self.assertIn("token=[REDACTED]", items[0]["message"])
        self.assertIn("https://[REDACTED]@example.test/path", items[1]["message"])
        status_stop = stop_of("/p", {**state, "stop": stop})
        self.assertEqual(status_stop["review_findings"], items)
        texts = ["\n".join(render_stop(stop))]
        for view in ({"status": "DESIGN_REVISION_REQUIRED", "stop": status_stop},):
            output = StringIO()
            with contextlib.redirect_stdout(output):
                display(view, False)
            texts.append(output.getvalue())
        texts.append(render_log({"change_id": "FEAT-1", "change_status": "APPROVED",
                                 "run_status": state["status"], "attempt": 1,
                                 "calls": [], "failures": [], "stop": status_stop}))
        for text in texts:
            for item in items:
                self.assertIn(item["summary"], text)
            for secret in ("fixture-secret", "fixture-user:fixture-pass"):
                self.assertNotIn(secret, text)


if __name__ == "__main__":
    unittest.main()
