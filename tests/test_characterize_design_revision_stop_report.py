"""Stable stop-report behavior around the design-revision presentation change."""

from copy import deepcopy
import unittest

from storyloop.stop_report import build_stop, render_stop, stop_of


EXISTING_FIELDS = {
    "status", "phase", "completed", "last_verdict", "attempts", "blocker",
    "evidence", "facts", "questions", "options", "resume", "routing",
}


def run_state(status):
    findings = [
        {"target": "design", "blocking": True, "message": "Revise scope",
         "location": "docs/design.md:7"},
        {"target": "design", "blocking": False, "message": "Optional note",
         "location": "docs/design.md:9"},
    ]
    return {
        "change_id": "FEAT-1", "status": status, "attempt": 1,
        "contract_commit": "abc", "red_evidence_sha256": "def",
        "failures": [], "findings": findings,
        "planning": {"consistency": {"findings": deepcopy(findings)}},
    }


class DesignRevisionStopCharacterization(unittest.TestCase):
    def test_build_preserves_state_and_existing_replan_contract(self):
        state = run_state("DESIGN_REVISION_REQUIRED")
        original = deepcopy(state)
        stop = build_stop("/p", state, state["status"], {"phase": "planning"})

        self.assertEqual(state, original)
        self.assertTrue(EXISTING_FIELDS <= stop.keys())
        self.assertEqual(stop["status"], "DESIGN_REVISION_REQUIRED")
        self.assertEqual(stop["phase"], "planning")
        command = "storyloop run /p --change FEAT-1 --replan --reason '<方案修订理由>'"
        self.assertIn({"action": "明确决定后继续", "command": command}, stop["options"])
        self.assertEqual(stop["resume"], command)
        rendered = render_stop(stop)
        self.assertEqual(rendered[0], "阻塞点：DESIGN_REVISION_REQUIRED")
        self.assertEqual(rendered[-1], f"恢复：{command}")

    def test_reading_saved_design_revision_does_not_change_run_or_findings(self):
        state = run_state("DESIGN_REVISION_REQUIRED")
        state["stop"] = build_stop("/p", state, state["status"], {"phase": "planning"})
        saved = deepcopy(state)

        shown = stop_of("/p", state)

        self.assertEqual(state, saved)
        self.assertEqual(state["planning"]["consistency"]["findings"], saved["findings"])
        self.assertTrue(EXISTING_FIELDS <= shown.keys())
        self.assertEqual(shown["status"], "DESIGN_REVISION_REQUIRED")
        self.assertEqual(shown["resume"], saved["stop"]["resume"])

    def test_other_stops_keep_fields_commands_and_render_order(self):
        cases = (
            ("DESIGN_APPROVAL_REQUIRED", "DESIGN_APPROVAL_REQUIRED",
             ["storyloop approve /p --change FEAT-1 --design '<plan-id>'"]),
            ("NO_PROGRESS", "根因调查后失败特征仍不变", [
                "storyloop acceptance-repair /p --change FEAT-1 --reason '<原因>'",
                "storyloop change /p --supersede FEAT-1 --message '<修订意见>' "
                "--reason '<为什么是合同的问题>'",
                "storyloop run /p --change FEAT-1",
            ]),
        )
        for status, blocker, commands in cases:
            with self.subTest(status=status):
                state = run_state(status)
                before = deepcopy(state)
                stop = build_stop("/p", state, status, {})
                self.assertEqual(state, before)
                self.assertEqual(set(stop), EXISTING_FIELDS)
                self.assertEqual([option["command"] for option in stop["options"]], commands)
                self.assertEqual(stop["resume"], commands[-1])
                self.assertEqual(render_stop(stop), [
                    f"阻塞点：{blocker}", "可选操作：",
                    *[f"  {index}. {option['action']}：{command}"
                      for index, (option, command) in enumerate(zip(stop["options"], commands), 1)],
                    f"恢复：{commands[-1]}",
                ])


if __name__ == "__main__":
    unittest.main()
