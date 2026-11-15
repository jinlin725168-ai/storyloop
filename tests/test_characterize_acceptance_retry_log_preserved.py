"""Existing public log behavior that the retry history projection must preserve."""

from __future__ import annotations

import io
import json
import unittest
from unittest.mock import patch

from storyloop import cli
from tests.test_structured_output import approved_change


class AcceptanceRetryLogPreservationTests(unittest.TestCase):
    def setUp(self):
        self.temporary, self.root, self.change_id = approved_change()
        self.addCleanup(self.temporary.cleanup)
        self.change_path = (self.root / ".storyloop/state/changes"
                            / f"{self.change_id}.json")
        self.run_path = (self.root / ".storyloop/state/runs"
                         / f"{self.change_id}.json")
        self.run_path.parent.mkdir(parents=True, exist_ok=True)
        self.run_path.write_text(json.dumps({
            "status": "PASSED", "attempt": 2,
            "failures": [{"attempt": 1, "contract": {"message": "contract rejected"}}],
            "acceptance_retry": {"version": 1, "rounds": [{
                "repair_count": 0, "finding_ids": ["A"], "signatures": []
            }]},
        }), encoding="utf-8")

    def _cli_output(self, command: str, *, json_output: bool = False) -> dict | str:
        args = [command, str(self.root), "--change", self.change_id]
        if json_output:
            args.append("--json")
        with patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(cli.main(args), 0)
        return json.loads(output.getvalue()) if json_output else output.getvalue()

    def test_log_keeps_existing_json_fields_and_does_not_write_state(self):
        before_change = self.change_path.read_bytes()
        before_run = self.run_path.read_bytes()

        result = self._cli_output("log", json_output=True)

        self.assertEqual(result["change_status"], "APPROVED")
        self.assertEqual(result["run_status"], "PASSED")
        self.assertEqual(result["failures"][0]["gate"], "contract")
        self.assertEqual(result["failures"][0]["reason"], "contract rejected")
        self.assertIn("storyloop report", result["next"])
        self.assertEqual(self.change_path.read_bytes(), before_change)
        self.assertEqual(self.run_path.read_bytes(), before_run)

    def test_log_keeps_existing_text_sections(self):
        text = self._cli_output("log")

        self.assertIn(f"{self.change_id}  合同 APPROVED  运行 PASSED", text)
        self.assertIn("代理调用：", text)
        self.assertIn("失败的尝试：", text)
        self.assertIn("contract rejected", text)
        self.assertIn("合计 0 次调用", text)
        self.assertIn("下一步: 报告：storyloop report", text)


if __name__ == "__main__":
    unittest.main()
