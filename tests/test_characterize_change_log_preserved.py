"""Existing log fields, paragraphs, and read-only behavior before intervention view."""

from __future__ import annotations

import io
import json
import unittest
from unittest.mock import patch

from storyloop import cli
from tests.test_structured_output import approved_change


class ChangeLogPreservationTests(unittest.TestCase):
    def setUp(self):
        self.temporary, self.root, self.change_id = approved_change()
        self.addCleanup(self.temporary.cleanup)

    def log(self, *, as_json: bool):
        args = ["log", str(self.root), "--change", self.change_id]
        if as_json:
            args.append("--json")
        with patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(cli.main(args), 0)
        return json.loads(output.getvalue()) if as_json else output.getvalue()

    def test_json_keeps_existing_change_identity_and_diagnostic_fields(self):
        result = self.log(as_json=True)
        self.assertEqual(result["status"], "LOG")
        self.assertEqual(result["change_id"], self.change_id)
        self.assertIn("change_status", result)
        self.assertIn("calls", result)
        self.assertIn("failures", result)
        self.assertIn("telemetry", result)
        self.assertIn("next", result)

    def test_text_keeps_existing_header_calls_and_summary(self):
        output = self.log(as_json=False)
        self.assertIn(f"{self.change_id}  合同 ", output)
        self.assertIn("代理调用：", output)
        self.assertIn("合计 0 次调用，代理耗时 0.0 分钟", output)

    def test_both_log_forms_preserve_change_state_bytes(self):
        path = self.root / ".storyloop/state/changes" / f"{self.change_id}.json"
        before = path.read_bytes()
        self.log(as_json=True)
        self.log(as_json=False)
        self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
