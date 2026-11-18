"""Trial F (sqlite-utils, rows --exclude): what a person reads at the first two decisions."""
import unittest
from pathlib import Path
import tempfile

from storyloop.changes import write_change_preview
from storyloop.stop_report import render_stop
from tests.support import product


class TrialViewTests(unittest.TestCase):
    def test_draft_shows_the_assumptions_and_consumers_the_checklist_asks_about(self):
        value = product()
        value["assumptions"] = [{"id": "AS1", "statement": "剩余列按表定义顺序输出", "status": "assumed"}]
        value["consumers"] = [{"id": "C2", "consumer": "CLI 参考文档", "path": "docs/cli.rst",
                               "in_scope": True, "verification": "cog --check"}]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            write_change_preview(root, {"change_id": "FEAT-1", "product": value})
            text = (root / ".storyloop/drafts/FEAT-1/proposal.md").read_text(encoding="utf-8")
        self.assertIn("AS1 [assumed] 剩余列按表定义顺序输出", text)
        self.assertIn("C2 CLI 参考文档", text)

    def test_a_draft_without_them_keeps_its_old_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            write_change_preview(root, {"change_id": "FEAT-1", "product": product()})
            text = (root / ".storyloop/drafts/FEAT-1/proposal.md").read_text(encoding="utf-8")
        self.assertNotIn("## 假设", text)
        self.assertTrue(text.endswith("## 待决问题\n\n- 无\n"))

    def test_routing_evidence_is_one_short_line_each_and_capped(self):
        evidence = [{"field": f"risks.r{index}", "value": "no", "source": "role",
                     "locations": ["sqlite_utils/cli.py:1"], "commit": "c" * 40} for index in range(28)]
        lines = render_stop({"blocker": "b", "evidence": evidence, "resume": "storyloop change"})
        shown = [line for line in lines if line.startswith("  - ")]
        self.assertEqual(len(shown), 9)
        self.assertEqual(shown[0], "  - risks.r0=no（sqlite_utils/cli.py:1）")
        self.assertIn("另 20 条", shown[-1])


class AutoRepairTests(unittest.TestCase):
    def _run(self, questions, blockers):
        from unittest import mock
        from storyloop import product_cli
        calls = []
        state = {"change_id": "FEAT-1", "product": {"questions": questions}}
        with mock.patch.object(product_cli, "draft_blockers", return_value=blockers), \
                mock.patch.object(product_cli, "load_change", return_value={**state, "revision": 2}):
            result = product_cli.auto_repair(Path("."), state, lambda *args: calls.append(args))
        return calls, result

    def test_role_fixable_blockers_go_back_to_the_product_role_once(self):
        calls, result = self._run([], [{"id": "CHK048", "dimension": "Coverage",
                                        "text": "S1 rule 6 has a positive example"}])
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0][2].startswith("引擎自动退回（无需人工）"))
        self.assertIn("CHK048 S1 rule 6 has a positive example", calls[0][2])
        self.assertEqual(result["revision"], 2)

    def test_a_failed_automatic_round_keeps_the_draft(self):
        from unittest import mock
        from storyloop import product_cli
        from storyloop.models import StoryLoopError
        state = {"change_id": "FEAT-1", "product": {"questions": []}}

        def fail(*args):
            raise StoryLoopError("agent down", code="AGENT_UNAVAILABLE")
        with mock.patch.object(product_cli, "draft_blockers",
                               return_value=[{"id": "C", "dimension": "Coverage", "text": "x"}]):
            self.assertIs(product_cli.auto_repair(Path("."), state, fail), state)

    def test_open_questions_and_clarity_items_stay_with_the_person(self):
        self.assertEqual(self._run([{"id": "Q1"}], [{"id": "C", "dimension": "Coverage", "text": "x"}])[0], [])
        self.assertEqual(self._run([], [{"id": "C", "dimension": "Clarity", "text": "AS1"}])[0], [])


if __name__ == "__main__":
    unittest.main()
