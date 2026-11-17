"""Stops name their class and an unavailable agent names why (roadmap C1/C3, 2026-11-17).

L3 on sqlite-utils: the retrospective call failed with "OAuth session expired" and the stop
read like any other agent crash, so an operator could not tell that re-login was the fix; and
B4 accounting had to separate engine/infrastructure stops from contract or oracle stops by hand.
"""

import unittest

from storyloop.intervention_events import STOP_CLASSES, stop_class, unavailable_reason


class StopClassTests(unittest.TestCase):
    def test_every_stop_status_maps_to_one_class(self):
        cases = {"AGENT_UNAVAILABLE": "infrastructure", "AGENT_CONTRACT_INVALID": "infrastructure",
                 "WORKSPACE_TAINTED": "infrastructure", "READINESS_APPROVAL_REQUIRED": "configuration",
                 "BASE_CHANGE_REQUIRED": "configuration", "NEEDS_SPEC_FIX": "contract",
                 "NEEDS_INPUT": "contract", "ORACLE_DEFECT": "oracle",
                 "CHARACTERIZATION_FAILED": "oracle", "BUDGET_EXHAUSTED": "implementation",
                 "DESIGN_APPROVAL_REQUIRED": "human", "MANUAL_ACCEPTANCE_REQUIRED": "human"}
        for status, expected in cases.items():
            with self.subTest(status=status):
                self.assertEqual(stop_class(status), expected)
        self.assertEqual(stop_class("SOMETHING_NEW"), "other")
        statuses = [status for members in STOP_CLASSES.values() for status in members]
        self.assertEqual(len(statuses), len(set(statuses)))

    def test_unavailable_reason_reads_the_agent_message(self):
        cases = {
            "exit 1 | claude review ended with success: Failed to authenticate: OAuth session expired": "auth",
            "exit 1 | Not logged in · Please run /login": "auth",
            "exit 1 | You've hit your usage limit. Upgrade or try again at 4:52 PM": "quota",
            "exit 1 | stream error: 429 Too Many Requests rate limit": "quota",
            "exit 1 | getaddrinfo ENOTFOUND api.anthropic.com": "network",
            "timed_out": "timeout",
            "Agent executable is unavailable": "missing",
            "exit 3 | Traceback (most recent call last)": "crash",
        }
        for message, expected in cases.items():
            with self.subTest(message=message):
                self.assertEqual(unavailable_reason(message), expected)


class StopReportClassTests(unittest.TestCase):
    def test_stop_report_carries_class_and_reason_and_renders_them(self):
        from storyloop.stop_report import build_stop, render_stop
        state = {"change_id": "FEAT-x", "status": "AGENT_UNAVAILABLE"}
        stop = build_stop("/p", state, "AGENT_UNAVAILABLE",
                          {"error": "exit 1 | Failed to authenticate: OAuth session expired"})
        self.assertEqual(stop["class"], "infrastructure")
        self.assertEqual(stop["reason"], "auth")
        text = "\n".join(render_stop(stop))
        self.assertIn("基础设施", text)
        self.assertIn("登录", text)


if __name__ == "__main__":
    unittest.main()
