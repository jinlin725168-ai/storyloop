"""Existing report contract for a linked base change."""

from copy import deepcopy
import shlex
import unittest

from storyloop.cli import parser
from storyloop.stop_report import build_stop, stop_of


class BaseStopReportCharacterization(unittest.TestCase):
    def test_report_keeps_approval_target_and_saved_state_unchanged(self):
        state = {
            "change_id": "FEAT-1",
            "status": "BASE_CHANGE_REQUIRED",
            "blocked_by": "BASE-1",
            "legacy_paths": ["modules/orders/service.py"],
            "source_branch": "main",
            "feature_branch": "feature/one",
            "attempt": 1,
            "failures": [],
        }
        original = deepcopy(state)
        report = build_stop("/p", state, "BASE_CHANGE_REQUIRED", state)
        self.assertEqual(state, original)
        command = report["options"][0]["command"]
        args = parser().parse_args(shlex.split(command)[1:])
        self.assertEqual((args.command, args.change_id), ("approve", "BASE-1"))
        self.assertIn("取回或丢弃暂存", report["options"][0]["action"])

        state["stop"] = deepcopy(report)
        saved = deepcopy(state)
        viewed = stop_of("/p", state)
        self.assertEqual(state, saved)
        self.assertEqual(viewed["options"][0]["command"], command)
        self.assertEqual(viewed["resume"], "storyloop run /p --change FEAT-1")


if __name__ == "__main__":
    unittest.main()
